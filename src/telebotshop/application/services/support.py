from datetime import timedelta
from uuid import UUID, uuid4, uuid5

from telebotshop.application.ports.inventory_crypto import InventoryCipher
from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.application.services.orders import validate_uuid
from telebotshop.domain.errors import AccessDenied, InvalidAction, InvalidInput
from telebotshop.domain.models.access import Access, Permission, ResourceScope, can
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import Page, validate_page
from telebotshop.domain.models.history import OrderView
from telebotshop.domain.models.identity import Role, validate_telegram_id
from telebotshop.domain.models.support import (
    PublicTerms,
    SupportMessage,
    SupportMessageContext,
    SupportPost,
    Ticket,
    TicketAction,
    TicketActionKind,
    TicketDetail,
    TicketKind,
    TicketState,
    TicketSummary,
    ticket_summary,
)


async def support_staff(work: UnitOfWork, actor_id: int, *, manager: bool = False) -> Access:
    validate_telegram_id(actor_id)
    await work.access.lock()
    actor = await work.access.get(actor_id)
    # This scope admits a queue only; the service filters SUPPORT rows by persisted assignee.
    if (
        actor is None
        or not can(actor, Permission.MANAGE_TICKETS, ResourceScope(actor_id))
        or (manager and actor.role == Role.SUPPORT)
    ):
        raise AccessDenied("Support unavailable")
    return actor


async def scoped_ticket(
    work: UnitOfWork,
    actor_id: int,
    ticket_id: UUID,
    staff: bool,
    *,
    manager: bool = False,
) -> tuple[Ticket, UUID]:
    validate_telegram_id(actor_id)
    validate_uuid(ticket_id)
    if type(staff) is not bool:
        raise InvalidInput("Invalid support scope")
    if staff:
        actor = await support_staff(work, actor_id, manager=manager)
        author_id = actor.user_id
    else:
        profile = await work.identity.get_profile(actor_id)
        if profile is None:
            raise AccessDenied("Ticket unavailable")
        author_id = profile.id
    ticket = await work.support.get(ticket_id, for_update=True)
    if ticket is None:
        raise AccessDenied("Ticket unavailable")
    if staff:
        scope = ResourceScope(actor_id if ticket.assignee_id == actor.user_id else None)
        if not can(actor, Permission.MANAGE_TICKETS, scope):
            raise AccessDenied("Ticket unavailable")
    elif ticket.customer_id != author_id:
        raise AccessDenied("Ticket unavailable")
    return ticket, author_id


def require_open(ticket: Ticket) -> None:
    if ticket.state != TicketState.OPEN:
        raise InvalidAction("Ticket closed")


class SupportService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        cipher: InventoryCipher,
        terms: PublicTerms | None = None,
    ) -> None:
        self.uow, self.cipher, self.public_terms = uow, cipher, terms

    async def contact(self) -> str | None:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            return (await work.access.lock()).support_username

    def terms(self, page: int = 0) -> tuple[str | None, bool]:
        validate_page(page)
        if self.public_terms is None:
            return None, False
        pages = self.public_terms.pages
        if page >= len(pages):
            raise InvalidInput("Terms page unavailable")
        return pages[page], page + 1 < len(pages)

    async def order_context(self, actor_id: int, order_id: UUID) -> OrderView:
        validate_telegram_id(actor_id)
        validate_uuid(order_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            profile = await work.identity.get_profile(actor_id)
            view = None if profile is None else await work.history.detail(order_id, profile.id)
            if view is None:
                raise AccessDenied("Order unavailable")
            return view

    async def list(
        self, actor_id: int, page: int = 0, *, staff: bool = False
    ) -> Page[TicketSummary]:
        validate_page(page)
        validate_telegram_id(actor_id)
        if type(staff) is not bool:
            raise InvalidInput("Invalid support scope")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            if staff:
                actor = await support_staff(work, actor_id)
                return await work.support.list(
                    page,
                    staff=True,
                    assignee_id=(actor.user_id if actor.role == Role.SUPPORT else None),
                )
            profile = await work.identity.get_profile(actor_id)
            if profile is None:
                raise AccessDenied("Ticket unavailable")
            return await work.support.list(page, customer_id=profile.id)

    async def create(
        self,
        actor_id: int,
        kind: TicketKind,
        text: str,
        request_id: UUID,
        *,
        order_id: UUID | None = None,
    ) -> TicketSummary:
        validate_telegram_id(actor_id)
        validate_uuid(request_id)
        if not isinstance(kind, TicketKind):
            raise InvalidInput("Invalid ticket kind")
        if order_id is not None:
            validate_uuid(order_id)
        message = SupportMessage(text)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            customer_id = await work.orders.lock_buyer(actor_id)
            if customer_id is None:
                raise AccessDenied("Customer unavailable")
            existing = await work.support.by_request(customer_id, request_id)
            if existing is not None:
                if existing.kind != kind or existing.order_id != order_id:
                    raise InvalidAction("Request belongs to another ticket")
                return ticket_summary(existing)
            if order_id is not None and await work.history.detail(order_id, customer_id) is None:
                raise AccessDenied("Order unavailable")
            now = await work.orders.now()
            ticket = Ticket(
                uuid4(), customer_id, kind, order_id, None, 0, TicketState.OPEN, now, now
            )
            await work.inventory.check_keys(self.cipher.proof())
            context = SupportMessageContext(ticket.id, customer_id, uuid4(), customer_id)
            await work.support.create(ticket, request_id)
            await work.support.post(context, self.cipher.seal(context, message), request_id, now)
            await work.support.audit(
                customer_id, ticket.id, "support.created", uuid5(ticket.id, "created")
            )
            result = ticket_summary(await work.support.get(ticket.id))
            await work.commit()
            return result

    async def detail(
        self,
        actor_id: int,
        ticket_id: UUID,
        page: int = 0,
        *,
        staff: bool = False,
    ) -> TicketDetail:
        validate_page(page)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            ticket, author_id = await scoped_ticket(work, actor_id, ticket_id, staff)
            encrypted = await work.support.messages(ticket.id, page)
            posts = []
            for post in encrypted.items:
                payload = self.cipher.open(post.context, post.sealed)
                if not isinstance(payload, SupportMessage):
                    raise InvalidAction("Invalid support message")
                posts.append(
                    SupportPost(
                        post.context.message_id, payload.text, post.context.staff, post.created_at
                    )
                )
            order = None
            if ticket.order_id is not None:
                order = await work.history.detail(ticket.order_id, ticket.customer_id)
                if order is None:
                    raise InvalidAction("Linked order unavailable")
            await work.support.audit(author_id, ticket.id, "support.viewed", uuid4())
            await work.commit()
            return TicketDetail(
                ticket_summary(ticket), Page(tuple(posts), page, encrypted.has_more), order
            )

    async def reply(
        self,
        actor_id: int,
        ticket_id: UUID,
        text: str,
        request_id: UUID,
        *,
        staff: bool = False,
    ) -> UUID:
        validate_uuid(request_id)
        message = SupportMessage(text)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            ticket, author_id = await scoped_ticket(work, actor_id, ticket_id, staff)
            existing = await work.support.post_request(author_id, request_id)
            if existing is not None:
                if existing[1] != ticket.id:
                    raise InvalidAction("Reply belongs to another ticket")
                return existing[0]
            require_open(ticket)
            await work.inventory.check_keys(self.cipher.proof())
            context = SupportMessageContext(
                ticket.id, ticket.customer_id, uuid4(), author_id, staff
            )
            await work.support.post(
                context, self.cipher.seal(context, message), request_id, await work.orders.now()
            )
            await work.support.audit(
                author_id, ticket.id, "support.replied", uuid5(context.message_id, "reply")
            )
            await work.commit()
            return context.message_id

    async def _target(self, work: UnitOfWork, target_id: UUID | None) -> None:
        if target_id is None:
            return
        target = await work.access.get_by_id(target_id)
        if target is None or not can(
            target, Permission.MANAGE_TICKETS, ResourceScope(target.telegram_id)
        ):
            raise InvalidAction("Assignee lacks current support grant")

    async def prepare_assignment(
        self, actor_id: int, ticket_id: UUID, target_telegram_id: int | None
    ) -> TicketAction:
        if target_telegram_id is not None:
            validate_telegram_id(target_telegram_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            ticket, author_id = await scoped_ticket(work, actor_id, ticket_id, True, manager=True)
            require_open(ticket)
            target_id = None
            if target_telegram_id is not None:
                target = await work.access.get(target_telegram_id)
                if target is None:
                    raise InvalidAction("Assignee unavailable")
                target_id = target.user_id
            await self._target(work, target_id)
            action = TicketAction(
                uuid4(),
                author_id,
                ticket.id,
                TicketActionKind.ASSIGN,
                True,
                target_id,
                ticket.version,
                await work.orders.now() + timedelta(minutes=10),
            )
            await work.support.save_action(action)
            await work.commit()
            return action

    async def prepare_close(
        self, actor_id: int, ticket_id: UUID, *, staff: bool = False
    ) -> TicketAction:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            ticket, author_id = await scoped_ticket(work, actor_id, ticket_id, staff)
            require_open(ticket)
            action = TicketAction(
                uuid4(),
                author_id,
                ticket.id,
                TicketActionKind.CLOSE,
                staff,
                None,
                ticket.version,
                await work.orders.now() + timedelta(minutes=10),
            )
            await work.support.save_action(action)
            await work.commit()
            return action

    async def _action(self, work: UnitOfWork, actor_id: int, action_id: UUID):
        validate_telegram_id(actor_id)
        validate_uuid(action_id)
        profile = await work.identity.get_profile(actor_id)
        action = await work.support.action(action_id)
        if profile is None or action is None or action.actor_id != profile.id:
            raise AccessDenied("Support action unavailable")
        ticket, author_id = await scoped_ticket(
            work,
            actor_id,
            action.ticket_id,
            action.staff,
            manager=action.kind == TicketActionKind.ASSIGN,
        )
        action = await work.support.action(action.id, for_update=True)
        return ticket, author_id, action

    async def confirm(self, actor_id: int, action_id: UUID) -> bool:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            ticket, author_id, action = await self._action(work, actor_id, action_id)
            if action.status == ActionStatus.APPLIED:
                return False
            require_open(ticket)
            now = await work.orders.now()
            if (
                action.status != ActionStatus.PENDING
                or action.expires_at <= now
                or action.expected_version != ticket.version
            ):
                raise InvalidAction("Support confirmation expired or changed")
            if action.kind == TicketActionKind.ASSIGN:
                await self._target(work, action.target_id)
            await work.support.finish_action(action, now)
            await work.support.audit(
                author_id,
                ticket.id,
                f"support.{action.kind.value.lower()}",
                uuid5(action.id, "applied"),
            )
            await work.commit()
            return True

    async def cancel(self, actor_id: int, action_id: UUID) -> None:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            ticket, author_id, action = await self._action(work, actor_id, action_id)
            if action.status == ActionStatus.APPLIED:
                raise InvalidAction("Support action already applied")
            if action.status == ActionStatus.CANCELLED:
                return
            await work.support.finish_action(action, await work.orders.now(), cancelled=True)
            await work.support.audit(
                author_id, ticket.id, "support.cancelled", uuid5(action.id, "cancelled")
            )
            await work.commit()
