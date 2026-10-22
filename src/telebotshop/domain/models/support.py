from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import Page, validate_text
from telebotshop.domain.models.history import OrderView
from telebotshop.domain.models.inventory import SealedPayload


class TicketKind(StrEnum):
    GENERAL = "GENERAL"
    PAYMENT = "PAYMENT"
    DELIVERY = "DELIVERY"


class TicketState(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class TicketActionKind(StrEnum):
    ASSIGN = "ASSIGN"
    CLOSE = "CLOSE"


@dataclass(frozen=True, repr=False)
class SupportMessage:
    text: str

    def __post_init__(self) -> None:
        validate_text(self.text, 2000)
        if len(self.text.encode("utf-8")) > 2000:
            raise InvalidInput("Support message too large")


def support_message_from_record(record: object) -> SupportMessage:
    if not isinstance(record, dict) or set(record) != {"text"}:
        raise InvalidInput("Invalid support message")
    return SupportMessage(record["text"])


@dataclass(frozen=True, repr=False)
class SupportMessageContext:
    ticket_id: UUID
    customer_id: UUID
    message_id: UUID
    author_id: UUID
    staff: bool = False

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(value, UUID)
                for value in (self.ticket_id, self.customer_id, self.message_id, self.author_id)
            )
            or type(self.staff) is not bool
        ):
            raise InvalidInput("Invalid support context")


@dataclass(frozen=True, repr=False)
class Ticket:
    id: UUID
    customer_id: UUID
    kind: TicketKind
    order_id: UUID | None
    assignee_id: UUID | None
    version: int
    state: TicketState
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, repr=False)
class TicketSummary:
    id: UUID
    kind: TicketKind
    order_id: UUID | None
    state: TicketState
    version: int
    assigned: bool
    created_at: datetime
    updated_at: datetime


def ticket_summary(ticket: Ticket) -> TicketSummary:
    return TicketSummary(
        ticket.id,
        ticket.kind,
        ticket.order_id,
        ticket.state,
        ticket.version,
        ticket.assignee_id is not None,
        ticket.created_at,
        ticket.updated_at,
    )


@dataclass(frozen=True, repr=False)
class SupportPost:
    id: UUID
    text: str
    from_staff: bool
    created_at: datetime


@dataclass(frozen=True, repr=False)
class EncryptedSupportPost:
    context: SupportMessageContext
    sealed: SealedPayload
    created_at: datetime


@dataclass(frozen=True, repr=False)
class TicketDetail:
    ticket: TicketSummary
    messages: Page[SupportPost]
    order: OrderView | None


@dataclass(frozen=True, repr=False)
class TicketAction:
    id: UUID
    actor_id: UUID
    ticket_id: UUID
    kind: TicketActionKind
    staff: bool
    target_id: UUID | None
    expected_version: int
    expires_at: datetime
    status: ActionStatus = ActionStatus.PENDING


@dataclass(frozen=True, repr=False)
class PublicTerms:
    text: str

    def __post_init__(self) -> None:
        validate_text(self.text, 24000)
        if len(self.text.encode("utf-8")) > 24000:
            raise InvalidInput("Public terms too large")

    @property
    def pages(self) -> tuple[str, ...]:
        pages, current, size = [], [], 0
        for character in self.text:
            width = len(character.encode("utf-8"))
            if size + width > 3000:
                pages.append("".join(current))
                current, size = [], 0
            current.append(character)
            size += width
        pages.append("".join(current))
        return tuple(pages)
