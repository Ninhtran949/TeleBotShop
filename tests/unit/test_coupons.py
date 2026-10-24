from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from telebotshop.domain.errors import InvalidAction, InvalidInput
from telebotshop.domain.models.coupons import Coupon, CouponDraft, DiscountKind, coupon_code
from telebotshop.domain.models.wallet import MAX_CREDITS

NOW = datetime(2026, 10, 24, tzinfo=UTC)


def draft(**changes):
    return CouponDraft(
        **dict(
            code="FAKE10",
            kind=DiscountKind.PERCENT,
            value=10,
            expires_at=NOW + timedelta(days=1),
            usage_cap=2,
            per_user_limit=1,
        )
        | changes
    )


@pytest.mark.parametrize("raw", ["ab", "A B", "A" * 33, "ＦＡＫＥ", "bad\ncode", "", None])
def test_coupon_codes_reject_untrusted_or_unbounded_input(raw):
    with pytest.raises(InvalidInput):
        coupon_code(raw)


def test_coupon_code_has_one_ascii_case_insensitive_identity():
    assert coupon_code("fake_10") == "FAKE_10"
    assert draft(code="fake10").code == "FAKE10"


@pytest.mark.parametrize(
    "changes",
    [
        {"value": True},
        {"value": 0},
        {"value": 100},
        {"value": 1.5},
        {"kind": "PERCENT"},
        {"kind": DiscountKind.FIXED, "value": MAX_CREDITS + 1},
        {"usage_cap": 0},
        {"per_user_limit": True},
        {"per_user_limit": 3},
        {"expires_at": NOW.replace(tzinfo=None)},
        {"product_id": "untrusted"},
        {"category_code": "bad code"},
        {"product_id": uuid4(), "category_code": "FAKE"},
    ],
)
def test_coupon_configuration_is_strict(changes):
    with pytest.raises(InvalidInput):
        draft(**changes)


@pytest.mark.parametrize(
    "kind,value,base,discount",
    [
        (DiscountKind.PERCENT, 33, 101, 33),
        (DiscountKind.FIXED, 99, 101, 99),
        (DiscountKind.PERCENT, 99, MAX_CREDITS, MAX_CREDITS * 99 // 100),
    ],
)
def test_coupon_quote_has_exact_integer_snapshot(kind, value, base, discount):
    coupon = Coupon(uuid4(), draft(kind=kind, value=value), True, uuid4())
    product_id = uuid4()
    quote = coupon.quote(product_id, "FAKE", base, NOW, 0, 0)
    assert quote.product_id == product_id and quote.coupon_id == coupon.id
    assert quote.base_credits == base and quote.discount_credits == discount
    assert quote.total_credits == base - discount
    assert type(quote.total_credits) is int


@pytest.mark.parametrize("problem", ["inactive", "expired", "cap", "user", "product", "category"])
def test_coupon_quote_enforces_current_eligibility(problem):
    config = draft()
    product_id = uuid4()
    if problem == "expired":
        config = replace(config, expires_at=NOW)
    elif problem == "product":
        config = replace(config, product_id=uuid4())
    elif problem == "category":
        config = replace(config, category_code="OTHER")
    coupon = Coupon(uuid4(), config, problem != "inactive", uuid4())
    with pytest.raises(InvalidAction):
        coupon.quote(
            product_id,
            "FAKE",
            100,
            NOW,
            2 if problem == "cap" else 0,
            1 if problem == "user" else 0,
        )


@pytest.mark.parametrize(
    "base,kind,value",
    [(1, DiscountKind.PERCENT, 1), (100, DiscountKind.FIXED, 100), (99, DiscountKind.FIXED, 100)],
)
def test_coupon_never_creates_zero_discount_or_free_order(base, kind, value):
    coupon = Coupon(uuid4(), draft(kind=kind, value=value), True, uuid4())
    with pytest.raises(InvalidAction):
        coupon.quote(uuid4(), "FAKE", base, NOW, 0, 0)
