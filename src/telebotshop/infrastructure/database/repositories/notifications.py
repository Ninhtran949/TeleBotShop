from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import RowMapping, Select, String, cast, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.notifications import NotificationRepository
from telebotshop.domain.models.notifications import (
    Notification,
    NotificationKind,
    NotificationResult,
    NotificationState,
)
from telebotshop.infrastructure.database.models.catalog import products
from telebotshop.infrastructure.database.models.identity import user_roles, users
from telebotshop.infrastructure.database.models.inventory import inventory_units
from telebotshop.infrastructure.database.models.notifications import notifications
from telebotshop.infrastructure.database.models.orders import invoice_intents, orders
from telebotshop.infrastructure.database.models.payments import fulfillments, payments
from telebotshop.infrastructure.database.models.refunds import refund_intents
from telebotshop.infrastructure.database.models.support import support_messages, support_tickets
from telebotshop.infrastructure.database.models.topups import settlement_events, topup_requests


def _sources(threshold: int, now: datetime) -> dict[NotificationKind, Select]:
    owner = select(user_roles.c.user_id).where(user_roles.c.role == "OWNER").scalar_subquery()

    def source(kind, target, revision, recipient=owner):
        return select(
            literal(kind.value).label("kind"),
            target.label("target_id"),
            recipient.label("recipient_id"),
            cast(revision, String).label("revision"),
        ).where(recipient.is_not(None))

    work_order = fulfillments.join(orders, fulfillments.c.order_id == orders.c.id)
    unpaid_refund = orders.c.status != "REFUNDED"
    available = (
        select(func.count())
        .select_from(inventory_units)
        .where(
            inventory_units.c.product_id == products.c.id, inventory_units.c.status == "AVAILABLE"
        )
        .correlate(products)
        .scalar_subquery()
    )
    latest = (
        select(support_messages.c.id)
        .where(support_messages.c.ticket_id == support_tickets.c.id)
        .order_by(support_messages.c.created_at.desc(), support_messages.c.id.desc())
        .limit(1)
        .correlate(support_tickets)
        .scalar_subquery()
    )
    ticket_posts = support_tickets.join(
        support_messages, support_messages.c.ticket_id == support_tickets.c.id
    )
    active_ticket = (support_tickets.c.state == "OPEN", support_messages.c.id == latest)
    return {
        NotificationKind.INVOICE_UNCERTAIN: source(
            NotificationKind.INVOICE_UNCERTAIN, orders.c.id, invoice_intents.c.attempt_id
        )
        .select_from(invoice_intents.join(orders, invoice_intents.c.order_id == orders.c.id))
        .where(
            orders.c.status == "AWAITING_PAYMENT",
            or_(
                invoice_intents.c.status == "UNCERTAIN",
                (invoice_intents.c.status == "SENDING")
                & (invoice_intents.c.started_at <= now - timedelta(seconds=30)),
            ),
        ),
        NotificationKind.PAYMENT_REVIEW: source(
            NotificationKind.PAYMENT_REVIEW, payments.c.id, literal("received")
        ).where(
            payments.c.status == "REQUIRES_REVIEW",
            ~exists(
                select(1).where(
                    refund_intents.c.payment_id == payments.c.id,
                    refund_intents.c.status == "SUCCEEDED",
                )
            ),
        ),
        NotificationKind.PAID_ATTENTION: source(
            NotificationKind.PAID_ATTENTION, orders.c.id, orders.c.attention_reason
        ).where(orders.c.attention_reason.is_not(None), unpaid_refund),
        NotificationKind.DELIVERY_ATTENTION: source(
            NotificationKind.DELIVERY_ATTENTION,
            orders.c.id,
            func.concat(fulfillments.c.state, ":", fulfillments.c.active_attempt_id),
        )
        .select_from(work_order)
        .where(fulfillments.c.state.in_(["FAILED", "UNCERTAIN"]), unpaid_refund),
        NotificationKind.MANUAL_WAITING: source(
            NotificationKind.MANUAL_WAITING, fulfillments.c.id, fulfillments.c.active_attempt_id
        )
        .select_from(work_order)
        .where(fulfillments.c.state == "AWAITING_MANUAL", ~fulfillments.c.hold, unpaid_refund),
        NotificationKind.BACKLOG: source(NotificationKind.BACKLOG, orders.c.id, fulfillments.c.id)
        .select_from(work_order)
        .where(
            fulfillments.c.state.in_(["READY", "RETRY_WAIT"]),
            fulfillments.c.due_at <= now - timedelta(minutes=5),
            ~fulfillments.c.hold,
            unpaid_refund,
        ),
        NotificationKind.LOW_STOCK: source(
            NotificationKind.LOW_STOCK,
            products.c.id,
            func.to_char(func.timezone("UTC", now), "YYYY-MM-DD"),
        ).where(
            products.c.sale_state == "ACTIVE",
            products.c.product_type.in_(["UNIQUE_KEY", "LICENSE"]),
            available <= threshold,
        ),
        NotificationKind.REFUND_UNCERTAIN: source(
            NotificationKind.REFUND_UNCERTAIN, refund_intents.c.payment_id, refund_intents.c.id
        ).where(
            or_(
                refund_intents.c.status == "UNCERTAIN",
                (refund_intents.c.status == "SENDING") & (refund_intents.c.deadline <= now),
            )
        ),
        NotificationKind.TICKET_ACTIVITY: source(
            NotificationKind.TICKET_ACTIVITY, support_tickets.c.id, support_messages.c.id
        )
        .select_from(ticket_posts)
        .where(*active_ticket, ~support_messages.c.staff),
        NotificationKind.TICKET_REPLY: source(
            NotificationKind.TICKET_REPLY,
            support_tickets.c.id,
            support_messages.c.id,
            support_tickets.c.customer_id,
        )
        .select_from(ticket_posts)
        .where(*active_ticket, support_messages.c.staff),
        NotificationKind.WALLET_TOPUP: source(
            NotificationKind.WALLET_TOPUP,
            topup_requests.c.id,
            topup_requests.c.journal_id,
            topup_requests.c.buyer_id,
        ).where(topup_requests.c.state == "CREDITED"),
        NotificationKind.TOPUP_REVIEW: source(
            NotificationKind.TOPUP_REVIEW, settlement_events.c.id, literal("review")
        ).where(
            or_(
                settlement_events.c.state == "REVIEW_REQUIRED",
                settlement_events.c.conflict.is_(True),
                settlement_events.c.reason == "OPERATOR_ESCALATION",
            )
        ),
    }


def _notification(row: RowMapping) -> Notification:
    return Notification(
        row["id"],
        NotificationKind(row["kind"]),
        row["target_id"],
        row["recipient_id"],
        row["revision"],
        NotificationState(row["state"]),
        row["deadline"],
        None if row["observed_state"] is None else NotificationState(row["observed_state"]),
    )


class SqlNotificationRepository(NotificationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def discover(self, threshold: int, limit: int, now: datetime) -> int:
        count = 0
        for statement in _sources(threshold, now).values():
            if count >= limit:
                break
            candidate = statement.subquery()
            duplicate = exists(
                select(1)
                .select_from(notifications)
                .where(
                    notifications.c.kind == candidate.c.kind,
                    notifications.c.target_id == candidate.c.target_id,
                    notifications.c.recipient_id == candidate.c.recipient_id,
                    notifications.c.revision == candidate.c.revision,
                )
            )
            values = (
                select(
                    func.gen_random_uuid(),
                    candidate.c.kind,
                    candidate.c.target_id,
                    candidate.c.recipient_id,
                    candidate.c.revision,
                )
                .where(~duplicate)
                .order_by(candidate.c.target_id)
                .limit(min(10, limit - count))
            )
            created = await self.session.scalars(
                insert(notifications)
                .from_select(["id", "kind", "target_id", "recipient_id", "revision"], values)
                .on_conflict_do_nothing(constraint="uq_notification_event")
                .returning(notifications.c.id)
            )
            count += len(created.all())
        return count

    async def next(self) -> Notification | None:
        row = (
            (
                await self.session.execute(
                    select(notifications)
                    .where(notifications.c.state == "READY")
                    .order_by(notifications.c.created_at, notifications.c.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _notification(row)

    async def get(self, identifier: UUID) -> Notification | None:
        row = (
            (
                await self.session.execute(
                    select(notifications).where(notifications.c.id == identifier).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _notification(row)

    async def recipient(self, note: Notification) -> int:
        return await self.session.scalar(
            select(users.c.telegram_id).where(users.c.id == note.recipient_id)
        )

    async def valid(self, note: Notification, threshold: int, now: datetime) -> bool:
        source = _sources(threshold, now)[note.kind].subquery()
        return await self.session.scalar(
            select(
                exists(
                    select(1)
                    .select_from(source)
                    .where(
                        source.c.target_id == note.target_id,
                        source.c.recipient_id == note.recipient_id,
                        source.c.revision == note.revision,
                    )
                )
            )
        )

    async def start(self, note: Notification, now: datetime) -> datetime:
        deadline = now + timedelta(seconds=60)
        await self.session.execute(
            update(notifications)
            .where(notifications.c.id == note.id)
            .values(state="SENDING", started_at=now, deadline=deadline)
        )
        return deadline

    async def suppress(self, note: Notification, now: datetime) -> None:
        await self.session.execute(
            update(notifications)
            .where(notifications.c.id == note.id)
            .values(state="SUPPRESSED", finished_at=now)
        )

    async def finish(
        self,
        note: Notification,
        result: NotificationResult,
        state: NotificationState,
        now: datetime,
    ) -> None:
        await self.session.execute(
            update(notifications)
            .where(notifications.c.id == note.id)
            .values(
                state=state,
                finished_at=func.coalesce(notifications.c.finished_at, now),
                observed_state=result.state,
                observed_at=now,
                message_id=result.message_id,
            )
        )

    async def expire(self, now: datetime) -> int:
        due = (
            select(notifications.c.id)
            .where(notifications.c.state == "SENDING", notifications.c.deadline <= now)
            .order_by(notifications.c.deadline)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        updated = await self.session.scalars(
            update(notifications)
            .where(notifications.c.id.in_(due))
            .values(state="UNCERTAIN", finished_at=now)
            .returning(notifications.c.id)
        )
        return len(updated.all())
