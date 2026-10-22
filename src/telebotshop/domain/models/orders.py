from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import ProductType

RESERVATION_TTL = timedelta(minutes=15)
INVOICE_CLAIM_TTL = timedelta(seconds=30)


class OrderStatus(StrEnum):
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    EXPIRED = "EXPIRED"
    PAID = "PAID"
    FULFILLING = "FULFILLING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class OrderAttention(StrEnum):
    RESERVATION_LOST = "RESERVATION_LOST"
    ENTITLEMENT_UNAVAILABLE = "ENTITLEMENT_UNAVAILABLE"


class CloseReason(StrEnum):
    CANCELLED = "CUSTOMER_CANCELLED"
    EXPIRED = "TTL_EXPIRED"
    REJECTED = "INVOICE_REJECTED"


class InvoiceStatus(StrEnum):
    READY = "READY"
    SENDING = "SENDING"
    SENT = "SENT"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, repr=False)
class Order:
    id: UUID
    buyer_id: UUID
    item_id: UUID
    product_id: UUID
    unit_id: UUID | None
    sku: str
    product_type: ProductType
    title: str
    description: str
    price_stars: int
    quantity: int
    created_at: datetime
    expires_at: datetime
    status: OrderStatus
    invoice_status: InvoiceStatus
    payload: str
    attempt_id: UUID | None = None
    invoice_started_at: datetime | None = None
    attention_reason: OrderAttention | None = None


@dataclass(frozen=True, repr=False)
class InvoiceRequest:
    order: Order
    buyer_telegram_id: int
    attempt_id: UUID


@dataclass(frozen=True, repr=False)
class InvoiceDelivery:
    status: InvoiceStatus
    message_id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, InvoiceStatus) or self.status not in {
            InvoiceStatus.SENT,
            InvoiceStatus.REJECTED,
            InvoiceStatus.UNCERTAIN,
        }:
            raise InvalidInput("Invalid invoice result")
        if (
            self.status == InvoiceStatus.SENT
            and (type(self.message_id) is not int or self.message_id <= 0)
            or self.status != InvoiceStatus.SENT
            and self.message_id is not None
        ):
            raise InvalidInput("Invalid invoice result")
