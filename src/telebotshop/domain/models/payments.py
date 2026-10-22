import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.identity import validate_telegram_id
from telebotshop.domain.models.orders import OrderAttention


def validate_receipt_text(value: str, limit: int, *, empty: bool = False) -> None:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise InvalidInput("Invalid payment receipt")
    try:
        valid = len(value.encode("utf-8")) <= limit
    except UnicodeEncodeError:
        valid = False
    if not valid or any(unicodedata.category(c) == "Cc" for c in value):
        raise InvalidInput("Invalid payment receipt")


@dataclass(frozen=True, repr=False)
class PaymentReceipt:
    charge_id: str
    payer_id: int | None
    currency: str
    amount: int
    payload: str
    provider_charge_id: str = ""
    is_recurring: bool = False
    is_first_recurring: bool = False
    subscription_expiration_date: int | None = None
    has_order_info: bool = False
    shipping_option_id: str | None = None

    def __post_init__(self) -> None:
        validate_receipt_text(self.charge_id, 1024)
        validate_receipt_text(self.currency, 16)
        validate_receipt_text(self.payload, 128, empty=True)
        validate_receipt_text(self.provider_charge_id, 1024, empty=True)
        if self.shipping_option_id is not None:
            validate_receipt_text(self.shipping_option_id, 1024, empty=True)
        if self.payer_id is not None:
            validate_telegram_id(self.payer_id)
        if type(self.amount) is not int or not -(2**63) <= self.amount < 2**63:
            raise InvalidInput("Invalid payment receipt")
        if any(
            type(flag) is not bool
            for flag in (
                self.is_recurring,
                self.is_first_recurring,
                self.has_order_info,
            )
        ):
            raise InvalidInput("Invalid payment receipt")
        expiry = self.subscription_expiration_date
        if expiry is not None and (type(expiry) is not int or not 0 <= expiry < 2**63):
            raise InvalidInput("Invalid payment receipt")

    def record(self) -> dict[str, str | int | bool | None]:
        return asdict(self)

    def digest(self) -> bytes:
        return hashlib.sha256(
            json.dumps(
                self.record(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).digest()


class PaymentStatus(StrEnum):
    MATCHED = "MATCHED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class PaymentOutcome(StrEnum):
    MATCHED = "MATCHED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


class ReviewReason(StrEnum):
    UNKNOWN_ORDER = "UNKNOWN_ORDER"
    MISSING_PAYER = "MISSING_PAYER"
    WRONG_BUYER = "WRONG_BUYER"
    WRONG_AMOUNT = "WRONG_AMOUNT"
    WRONG_CURRENCY = "WRONG_CURRENCY"
    EXTRA_CHARGE = "EXTRA_CHARGE"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_ORDER_STATE = "INVALID_ORDER_STATE"


@dataclass(frozen=True, repr=False)
class StoredPayment:
    id: UUID
    receipt: PaymentReceipt
    status: PaymentStatus
    order_id: UUID | None
    review_reason: ReviewReason | None = None


@dataclass(frozen=True, repr=False)
class PaymentResult:
    payment_id: UUID
    outcome: PaymentOutcome
    order_id: UUID | None
    attention_reason: OrderAttention | None = None


@dataclass(frozen=True, repr=False)
class CommittedWork:
    id: UUID
    order_id: UUID
    order_item_id: UUID
    payment_id: UUID
    inventory_unit_id: UUID | None
    buyer_id: UUID
    destination_telegram_id: int
    product_type: ProductType
    resource_id: UUID | None = None
