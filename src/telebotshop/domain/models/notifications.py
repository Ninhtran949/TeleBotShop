from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.identity import validate_telegram_id


class NotificationKind(StrEnum):
    PAYMENT_REVIEW = "PAYMENT_REVIEW"
    INVOICE_UNCERTAIN = "INVOICE_UNCERTAIN"
    PAID_ATTENTION = "PAID_ATTENTION"
    DELIVERY_ATTENTION = "DELIVERY_ATTENTION"
    MANUAL_WAITING = "MANUAL_WAITING"
    BACKLOG = "BACKLOG"
    LOW_STOCK = "LOW_STOCK"
    REFUND_UNCERTAIN = "REFUND_UNCERTAIN"
    TICKET_ACTIVITY = "TICKET_ACTIVITY"
    TICKET_REPLY = "TICKET_REPLY"
    WALLET_TOPUP = "WALLET_TOPUP"
    TOPUP_REVIEW = "TOPUP_REVIEW"


class NotificationState(StrEnum):
    READY = "READY"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    SUPPRESSED = "SUPPRESSED"


@dataclass(frozen=True, repr=False)
class Notification:
    id: UUID
    kind: NotificationKind
    target_id: UUID
    recipient_id: UUID
    revision: str
    state: NotificationState
    deadline: datetime | None
    observed_state: NotificationState | None


@dataclass(frozen=True, repr=False)
class NotificationRequest:
    id: UUID
    kind: NotificationKind
    target_id: UUID
    telegram_id: int
    deadline: datetime

    def __post_init__(self) -> None:
        validate_telegram_id(self.telegram_id)
        if (
            not all(isinstance(value, UUID) for value in (self.id, self.target_id))
            or not isinstance(self.kind, NotificationKind)
            or not isinstance(self.deadline, datetime)
            or self.deadline.tzinfo is None
            or self.deadline.utcoffset() is None
        ):
            raise InvalidInput("Invalid notification request")


@dataclass(frozen=True, repr=False)
class NotificationResult:
    state: NotificationState
    message_id: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, NotificationState)
            or self.state
            not in {NotificationState.SENT, NotificationState.FAILED, NotificationState.UNCERTAIN}
            or (
                self.state == NotificationState.SENT
                and (type(self.message_id) is not int or self.message_id <= 0)
            )
            or (self.state != NotificationState.SENT and self.message_id is not None)
        ):
            raise InvalidInput("Invalid notification result")
