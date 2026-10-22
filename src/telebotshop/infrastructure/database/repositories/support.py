from dataclasses import asdict

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.support import SupportRepository
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import Page
from telebotshop.domain.models.inventory import SealedPayload
from telebotshop.domain.models.support import (
    EncryptedSupportPost,
    SupportMessageContext,
    Ticket,
    TicketAction,
    TicketActionKind,
    TicketKind,
    TicketState,
    ticket_summary,
)
from telebotshop.infrastructure.database.models.identity import audit_logs
from telebotshop.infrastructure.database.models.support import (
    support_actions,
    support_messages,
    support_tickets,
)


def ticket_from_row(row):
    values = {key: row[key] for key in Ticket.__dataclass_fields__}
    values["kind"], values["state"] = TicketKind(values["kind"]), TicketState(values["state"])
    return Ticket(**values)


class SqlSupportRepository(SupportRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, ticket_id, *, for_update=False):
        query = select(support_tickets).where(support_tickets.c.id == ticket_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else ticket_from_row(row)

    async def by_request(self, customer_id, request_id):
        row = (
            (
                await self.session.execute(
                    select(support_tickets).where(
                        support_tickets.c.customer_id == customer_id,
                        support_tickets.c.request_id == request_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else ticket_from_row(row)

    async def list(self, page, *, customer_id=None, assignee_id=None, staff=False):
        query = select(support_tickets)
        if customer_id is not None:
            query = query.where(support_tickets.c.customer_id == customer_id)
        if staff:
            query = query.where(support_tickets.c.state == "OPEN")
        if assignee_id is not None:
            query = query.where(support_tickets.c.assignee_id == assignee_id)
        rows = (
            (
                await self.session.execute(
                    query.order_by(support_tickets.c.updated_at.desc(), support_tickets.c.id.desc())
                    .limit(6)
                    .offset(page * 5)
                )
            )
            .mappings()
            .all()
        )
        return Page(
            tuple(ticket_summary(ticket_from_row(row)) for row in rows[:5]), page, len(rows) > 5
        )

    async def create(self, ticket, request_id):
        await self.session.execute(
            insert(support_tickets).values(**asdict(ticket), request_id=request_id)
        )

    async def messages(self, ticket_id, page):
        rows = (
            (
                await self.session.execute(
                    select(support_messages, support_tickets.c.customer_id)
                    .select_from(support_messages.join(support_tickets))
                    .where(support_messages.c.ticket_id == ticket_id)
                    .order_by(support_messages.c.created_at.desc(), support_messages.c.id.desc())
                    .limit(2)
                    .offset(page)
                )
            )
            .mappings()
            .all()
        )
        return Page(
            tuple(
                EncryptedSupportPost(
                    SupportMessageContext(
                        row["ticket_id"],
                        row["customer_id"],
                        row["id"],
                        row["author_id"],
                        row["staff"],
                    ),
                    SealedPayload(row["key_version"], row["nonce"], row["ciphertext"]),
                    row["created_at"],
                )
                for row in rows[:1]
            ),
            page,
            len(rows) > 1,
        )

    async def post_request(self, author_id, request_id):
        row = (
            await self.session.execute(
                select(support_messages.c.id, support_messages.c.ticket_id).where(
                    support_messages.c.author_id == author_id,
                    support_messages.c.request_id == request_id,
                )
            )
        ).one_or_none()
        return None if row is None else tuple(row)

    async def post(self, context, sealed, request_id, now):
        await self.session.execute(
            insert(support_messages).values(
                id=context.message_id,
                ticket_id=context.ticket_id,
                author_id=context.author_id,
                staff=context.staff,
                request_id=request_id,
                created_at=now,
                **asdict(sealed),
            )
        )

    async def save_action(self, action):
        await self.session.execute(insert(support_actions).values(**asdict(action)))

    async def action(self, action_id, *, for_update=False):
        query = select(support_actions).where(support_actions.c.id == action_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        if row is None:
            return None
        values = {key: row[key] for key in TicketAction.__dataclass_fields__}
        values["kind"], values["status"] = (
            TicketActionKind(values["kind"]),
            ActionStatus(values["status"]),
        )
        return TicketAction(**values)

    async def finish_action(self, action, now, *, cancelled=False):
        await self.session.execute(
            update(support_actions)
            .where(support_actions.c.id == action.id)
            .values(
                status=ActionStatus.CANCELLED if cancelled else ActionStatus.APPLIED,
                completed_at=now,
            )
        )
        if cancelled:
            return
        values = {"version": support_tickets.c.version + 1, "updated_at": now}
        if action.kind == TicketActionKind.ASSIGN:
            values["assignee_id"] = action.target_id
        else:
            values.update(state=TicketState.CLOSED, closed_at=now)
        await self.session.execute(
            update(support_tickets).where(support_tickets.c.id == action.ticket_id).values(**values)
        )

    async def audit(self, actor_id, ticket_id, event, correlation_id):
        await self.session.execute(
            insert(audit_logs).values(
                actor_id=actor_id,
                target_id=ticket_id,
                action=event,
                correlation_id=correlation_id,
                changes={},
            )
        )
