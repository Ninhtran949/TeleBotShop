import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidAction, InvalidInput
from telebotshop.domain.models.catalog import validate_code
from telebotshop.domain.models.wallet import credits


class DiscountKind(StrEnum):
    PERCENT = "PERCENT"
    FIXED = "FIXED"


def coupon_code(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,31}", value) is None:
        raise InvalidInput("Invalid coupon code")
    return value.upper()


@dataclass(frozen=True, repr=False)
class CouponDraft:
    code: str
    kind: DiscountKind
    value: int
    expires_at: datetime
    usage_cap: int
    per_user_limit: int
    product_id: UUID | None = None
    category_code: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "code", coupon_code(self.code))
        if not isinstance(self.kind, DiscountKind):
            raise InvalidInput("Invalid discount kind")
        credits(self.value)
        if self.kind == DiscountKind.PERCENT and self.value > 99:
            raise InvalidInput("Invalid percentage")
        if (
            not isinstance(self.expires_at, datetime)
            or self.expires_at.utcoffset() is None
            or any(
                type(v) is not int or not 1 <= v <= 10**9
                for v in (self.usage_cap, self.per_user_limit)
            )
            or self.per_user_limit > self.usage_cap
            or self.product_id is not None
            and not isinstance(self.product_id, UUID)
            or self.product_id is not None
            and self.category_code is not None
        ):
            raise InvalidInput("Invalid coupon configuration")
        if self.category_code is not None:
            validate_code(self.category_code)


@dataclass(frozen=True, repr=False)
class CouponQuote:
    coupon_id: UUID
    code: str
    product_id: UUID
    base_credits: int
    discount_credits: int

    def __post_init__(self):
        if (
            not isinstance(self.coupon_id, UUID)
            or not isinstance(self.product_id, UUID)
            or coupon_code(self.code) != self.code
        ):
            raise InvalidInput("Invalid coupon snapshot")
        credits(self.base_credits)
        credits(self.discount_credits)
        credits(self.total_credits)

    @property
    def total_credits(self) -> int:
        return self.base_credits - self.discount_credits


@dataclass(frozen=True, repr=False)
class Coupon:
    id: UUID
    draft: CouponDraft
    active: bool
    created_by: UUID

    def quote(
        self,
        product_id: UUID,
        category_code: str,
        base_credits: int,
        now: datetime,
        total_used: int,
        user_used: int,
    ) -> CouponQuote:
        config = self.draft
        if (
            not self.active
            or now >= config.expires_at
            or total_used >= config.usage_cap
            or user_used >= config.per_user_limit
            or config.product_id is not None
            and config.product_id != product_id
            or config.category_code is not None
            and config.category_code != category_code
        ):
            raise InvalidAction("Coupon unavailable")
        credits(base_credits)
        discount = (
            base_credits * config.value // 100
            if config.kind == DiscountKind.PERCENT
            else config.value
        )
        if not 0 < discount < base_credits:
            raise InvalidAction("Coupon cannot discount this price")
        return CouponQuote(self.id, config.code, product_id, base_credits, discount)
