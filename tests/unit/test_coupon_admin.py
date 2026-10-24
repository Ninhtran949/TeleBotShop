from uuid import UUID

import pytest

from telebotshop.admin.coupons import parse_coupon
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.coupons import DiscountKind


@pytest.mark.parametrize(
    "scope", ["ALL", "PRODUCT:00000000-0000-0000-0000-000000000001", "CATEGORY:FAKE"]
)
def test_admin_coupon_form_preserves_integer_limits_and_explicit_scope(scope):
    result = parse_coupon(f"fake10 PERCENT 10 2050-01-01T12:00:00Z 5 1 {scope}")
    assert result.code == "FAKE10" and result.kind == DiscountKind.PERCENT
    assert result.value == 10 and result.usage_cap == 5 and result.per_user_limit == 1
    assert result.product_id == (UUID(int=1) if scope.startswith("PRODUCT") else None)
    assert result.category_code == ("FAKE" if scope.startswith("CATEGORY") else None)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "FAKE10 PERCENT",
        "FAKE10 PERCENT 10 2050-01-01 5 1 ALL",
        "FAKE10 PERCENT 10 2050-01-01T12:00:00Z 5 1 ALL:extra",
        "FAKE10 PERCENT 10 2050-01-01T12:00:00Z 5 1 PRODUCT:bad",
        "FAKE10 PERCENT 10 2050-01-01T12:00:00Z 5 1 CATEGORY:",
        "FAKE10 PERCENT 10 2050-01-01T12:00:00Z 5 1 OTHER",
        "FAKE10 PERCENT 100 2050-01-01T12:00:00Z 5 1 ALL",
        "FAKE10 PERCENT -10 2050-01-01T12:00:00Z 5 1 ALL",
        "FAKE10 PERCENT 10.5 2050-01-01T12:00:00Z 5 1 ALL",
        "FAKE10 PERCENT １０ 2050-01-01T12:00:00Z 5 1 ALL",
        "FAKE10 PERCENT 10 2050-01-01T12:00:00Z 1000000001 1 ALL",
        "A" * 501,
    ],
)
def test_admin_coupon_form_rejects_ambiguous_or_unbounded_input(text):
    with pytest.raises(InvalidInput):
        parse_coupon(text)
