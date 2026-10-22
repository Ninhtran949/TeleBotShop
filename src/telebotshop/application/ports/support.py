from datetime import datetime
from typing import Protocol
from uuid import UUID

from telebotshop.domain.models.catalog import Page
from telebotshop.domain.models.inventory import SealedPayload
from telebotshop.domain.models.support import (
    EncryptedSupportPost,
    SupportMessageContext,
    Ticket,
    TicketAction,
    TicketSummary,
)


class SupportRepository(Protocol):
    async def get(self, ticket_id: UUID, *, for_update: bool = False) -> Ticket | None: ...
    async def by_request(self, customer_id: UUID, request_id: UUID) -> Ticket | None: ...
    async def list(
        self,
        page: int,
        *,
        customer_id: UUID | None = None,
        assignee_id: UUID | None = None,
        staff: bool = False,
    ) -> Page[TicketSummary]: ...
    async def create(self, ticket: Ticket, request_id: UUID) -> None: ...
    async def messages(self, ticket_id: UUID, page: int) -> Page[EncryptedSupportPost]: ...
    async def post_request(self, author_id: UUID, request_id: UUID) -> tuple[UUID, UUID] | None: ...
    async def post(
        self,
        context: SupportMessageContext,
        sealed: SealedPayload,
        request_id: UUID,
        now: datetime,
    ) -> None: ...
    async def save_action(self, action: TicketAction) -> None: ...
    async def action(self, action_id: UUID, *, for_update: bool = False) -> TicketAction | None: ...
    async def finish_action(
        self, action: TicketAction, now: datetime, *, cancelled: bool = False
    ) -> None: ...
    async def audit(
        self, actor_id: UUID, ticket_id: UUID, event: str, correlation_id: UUID
    ) -> None: ...
