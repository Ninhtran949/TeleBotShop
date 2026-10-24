from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import DeliveryState


@dataclass(frozen=True, repr=False)
class OrderView:
    id: UUID
    title: str
    sku: str
    product_type: ProductType
    price_stars: int | None
    status: str
    created_at: datetime
    buyer_telegram_id: int
    work_id: UUID | None
    delivery_state: DeliveryState | None
    hold: bool
    resend_state: str | None = None
    price_credits: int | None = None

    @property
    def can_resend(self) -> bool:
        return (
            self.status == "DELIVERED"
            and self.delivery_state == DeliveryState.SENT
            and not self.hold
            and self.resend_state not in {"SENDING", "UNCERTAIN"}
            and self.product_type != ProductType.MANUAL_FULFILLMENT
        )


@dataclass(frozen=True)
class OrderDashboard:
    since: datetime
    total: int
    paid_stars: int
    status_counts: tuple[tuple[str, int], ...]
    paid_credits: int = 0


class ResendState(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, repr=False)
class ResendAction:
    id: UUID
    actor_id: UUID
    work_id: UUID
    attempt_id: UUID
    staff: bool
    expires_at: datetime
    status: ResendState = ResendState.PENDING
    deadline: datetime | None = None
