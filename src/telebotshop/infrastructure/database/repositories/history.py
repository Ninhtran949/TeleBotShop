from dataclasses import asdict
from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.history import HistoryRepository
from telebotshop.domain.models.catalog import Page, ProductType
from telebotshop.domain.models.fulfillment import DeliveryCode, DeliveryState
from telebotshop.domain.models.history import OrderDashboard, OrderView, ResendAction, ResendState
from telebotshop.infrastructure.database.models.history import resend_actions
from telebotshop.infrastructure.database.models.identity import audit_logs, users
from telebotshop.infrastructure.database.models.orders import order_items, orders
from telebotshop.infrastructure.database.models.payments import fulfillments, payments


def history_query(buyer_id: UUID | None = None, since: datetime | None = None):
    last_resend = (
        select(
            case(
                (
                    (resend_actions.c.status == "SENDING")
                    & (resend_actions.c.deadline <= func.clock_timestamp()),
                    "UNCERTAIN",
                ),
                else_=resend_actions.c.status,
            )
        )
        .where(resend_actions.c.work_id == fulfillments.c.id)
        .order_by(resend_actions.c.created_at.desc(), resend_actions.c.id.desc())
        .limit(1)
        .correlate(fulfillments)
        .scalar_subquery()
    )
    query = select(
        orders.c.id,
        order_items.c.title,
        order_items.c.sku,
        order_items.c.product_type,
        order_items.c.price_stars,
        orders.c.status,
        orders.c.created_at,
        users.c.telegram_id,
        fulfillments.c.id.label("work_id"),
        fulfillments.c.state,
        fulfillments.c.hold,
        last_resend.label("resend_state"),
    ).select_from(
        orders.join(order_items)
        .join(users)
        .outerjoin(fulfillments, fulfillments.c.order_item_id == order_items.c.id)
    )
    if buyer_id is not None:
        query = query.where(orders.c.buyer_id == buyer_id)
    if since is not None:
        query = query.where(orders.c.created_at >= since)
    return query


def order_view(row) -> OrderView:
    return OrderView(
        row.id,
        row.title,
        row.sku,
        ProductType(row.product_type),
        row.price_stars,
        row.status,
        row.created_at,
        row.telegram_id,
        row.work_id,
        DeliveryState(row.state) if row.state is not None else None,
        bool(row.hold),
        row.resend_state,
    )


class SqlHistoryRepository(HistoryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(self, buyer_id, page, since):
        rows = (
            await self.session.execute(
                history_query(buyer_id, since)
                .order_by(orders.c.created_at.desc(), orders.c.id.desc())
                .limit(6)
                .offset(page * 5)
            )
        ).all()
        return Page(tuple(order_view(row) for row in rows[:5]), page, len(rows) > 5)

    async def detail(self, order_id, buyer_id):
        row = (
            await self.session.execute(history_query(buyer_id).where(orders.c.id == order_id))
        ).one_or_none()
        return None if row is None else order_view(row)

    async def dashboard(self, since):
        counts = (
            await self.session.execute(
                select(orders.c.status, func.count())
                .where(orders.c.created_at >= since)
                .group_by(orders.c.status)
                .order_by(orders.c.status)
            )
        ).all()
        paid = await self.session.scalar(
            select(func.coalesce(func.sum(order_items.c.price_stars), 0))
            .select_from(orders.join(order_items))
            .where(
                orders.c.created_at >= since,
                select(payments.c.id)
                .where(payments.c.order_id == orders.c.id, payments.c.status == "MATCHED")
                .exists(),
            )
        )
        return OrderDashboard(since, sum(count for _, count in counts), paid, tuple(counts))

    async def resend(self, action_id, *, for_update=False):
        query = select(resend_actions).where(resend_actions.c.id == action_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else resend_from_row(row)

    async def active_resend(self, work_id):
        row = (
            (
                await self.session.execute(
                    select(resend_actions)
                    .where(
                        resend_actions.c.work_id == work_id,
                        resend_actions.c.status.in_(["PENDING", "SENDING", "UNCERTAIN"]),
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else resend_from_row(row)

    async def save_resend(self, action):
        await self.session.execute(insert(resend_actions).values(**asdict(action)))

    async def start_resend(self, action, now, deadline):
        await self.session.execute(
            update(resend_actions)
            .where(
                resend_actions.c.id == action.id,
                resend_actions.c.status == "PENDING",
            )
            .values(status="SENDING", started_at=now, deadline=deadline)
        )

    async def finish_resend(self, action, state, now, result=None):
        query = update(resend_actions).where(
            resend_actions.c.id == action.id,
            resend_actions.c.status == action.status,
        )
        observed = (
            {"observed_state": result.state, "message_id": result.message_id} if result else {}
        )
        if action.status == ResendState.UNCERTAIN:
            query = query.where(resend_actions.c.observed_state.is_(None)).values(**observed)
        else:
            code = (
                DeliveryCode.LEASE_EXPIRED
                if state == ResendState.UNCERTAIN
                and (result is None or result.state != DeliveryState.UNCERTAIN)
                else result.code
                if result
                else None
            )
            query = query.values(status=state, completed_at=now, error_code=code, **observed)
        return (await self.session.execute(query)).rowcount == 1

    async def audit_resend(self, action, order_id, event):
        await self.session.execute(
            insert(audit_logs).values(
                actor_id=action.actor_id,
                target_id=order_id,
                correlation_id=uuid5(action.id, event),
                action=event,
                changes={"work_id": str(action.work_id), "resend_id": str(action.id)},
            )
        )


def resend_from_row(row) -> ResendAction:
    return ResendAction(
        row["id"],
        row["actor_id"],
        row["work_id"],
        row["attempt_id"],
        row["staff"],
        row["expires_at"],
        ResendState(row["status"]),
        row["deadline"],
    )
