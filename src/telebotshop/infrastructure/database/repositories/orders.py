from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.orders import OrderRepository
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.inventory import InventoryState
from telebotshop.domain.models.orders import (
    INVOICE_CLAIM_TTL,
    CloseReason,
    InvoiceDelivery,
    InvoiceStatus,
    Order,
    OrderAttention,
    OrderStatus,
)
from telebotshop.infrastructure.database.models.identity import audit_logs, users
from telebotshop.infrastructure.database.models.inventory import inventory_units
from telebotshop.infrastructure.database.models.orders import (
    invoice_intents,
    order_items,
    orders,
    pre_checkout_approvals,
)


def order_query():
    return select(
        orders.c.id,
        orders.c.buyer_id,
        orders.c.status,
        orders.c.created_at,
        orders.c.expires_at,
        orders.c.attention_reason,
        order_items.c.id.label("item_id"),
        order_items.c.product_id,
        order_items.c.inventory_unit_id,
        order_items.c.sku,
        order_items.c.product_type,
        order_items.c.title,
        order_items.c.description,
        order_items.c.price_stars,
        order_items.c.quantity,
        invoice_intents.c.payload,
        invoice_intents.c.status.label("invoice_status"),
        invoice_intents.c.attempt_id,
        invoice_intents.c.started_at,
    ).select_from(orders.join(order_items).join(invoice_intents))


def order_from_row(row: RowMapping) -> Order:
    return Order(
        row["id"],
        row["buyer_id"],
        row["item_id"],
        row["product_id"],
        row["inventory_unit_id"],
        row["sku"],
        ProductType(row["product_type"]),
        row["title"],
        row["description"],
        row["price_stars"],
        row["quantity"],
        row["created_at"],
        row["expires_at"],
        OrderStatus(row["status"]),
        InvoiceStatus(row["invoice_status"]),
        row["payload"],
        row["attempt_id"],
        row["started_at"],
        OrderAttention(row["attention_reason"]) if row["attention_reason"] else None,
    )


class SqlOrderRepository(OrderRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def configure_timeouts(self) -> None:
        await self.session.execute(text("SET LOCAL lock_timeout = '2s'"))
        await self.session.execute(text("SET LOCAL statement_timeout = '5s'"))

    async def lock_buyer(self, telegram_id: int) -> UUID | None:
        return await self.session.scalar(
            select(users.c.id)
            .where(users.c.telegram_id == telegram_id)
            .with_for_update(key_share=True)
        )

    async def now(self) -> datetime:
        return await self.session.scalar(select(func.clock_timestamp()))

    async def by_action(self, buyer_id: UUID, action_id: UUID) -> Order | None:
        row = (
            (
                await self.session.execute(
                    order_query()
                    .where(orders.c.buyer_id == buyer_id, orders.c.checkout_action_id == action_id)
                    .with_for_update(of=orders)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else order_from_row(row)

    async def order(self, order_id: UUID, *, for_update: bool = False) -> Order | None:
        query = order_query().where(orders.c.id == order_id)
        if for_update:
            query = query.with_for_update(of=orders)
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else order_from_row(row)

    async def by_payload(self, payload: str) -> Order | None:
        row = (
            (
                await self.session.execute(
                    order_query()
                    .where(
                        invoice_intents.c.payload == payload,
                    )
                    .with_for_update(of=orders)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else order_from_row(row)

    async def available_unit(self, product_id: UUID) -> UUID | None:
        return await self.session.scalar(
            select(inventory_units.c.id)
            .where(
                inventory_units.c.product_id == product_id,
                inventory_units.c.status == InventoryState.AVAILABLE,
            )
            .order_by(inventory_units.c.id)
            .limit(1)
            .with_for_update()
        )

    async def save(self, order: Order, action_id: UUID) -> None:
        await self.session.execute(
            insert(orders).values(
                id=order.id,
                buyer_id=order.buyer_id,
                checkout_action_id=action_id,
                status=order.status,
                created_at=order.created_at,
                expires_at=order.expires_at,
            )
        )
        await self.session.execute(
            insert(order_items).values(
                id=order.item_id,
                order_id=order.id,
                product_id=order.product_id,
                inventory_unit_id=order.unit_id,
                sku=order.sku,
                product_type=order.product_type,
                title=order.title,
                description=order.description,
                price_stars=order.price_stars,
                quantity=order.quantity,
            )
        )
        await self.session.execute(
            insert(invoice_intents).values(
                order_id=order.id,
                payload=order.payload,
                status=InvoiceStatus.READY,
            )
        )
        await self.session.execute(
            update(inventory_units)
            .where(
                inventory_units.c.id == order.unit_id,
                inventory_units.c.status == InventoryState.AVAILABLE,
            )
            .values(
                status=InventoryState.RESERVED,
                reservation_item_id=order.item_id,
                reservation_expires_at=order.expires_at,
            )
        )

    async def has_reservation(self, order: Order, *, for_update: bool = False) -> bool:
        query = select(inventory_units.c.id).where(
            inventory_units.c.id == order.unit_id,
            inventory_units.c.product_id == order.product_id,
            inventory_units.c.status == InventoryState.RESERVED,
            inventory_units.c.reservation_item_id == order.item_id,
            inventory_units.c.reservation_expires_at == order.expires_at,
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query) is not None

    async def approved_query(self, order_id: UUID) -> str | None:
        return await self.session.scalar(
            select(pre_checkout_approvals.c.query_id).where(
                pre_checkout_approvals.c.order_id == order_id,
            )
        )

    async def save_approval(self, order_id: UUID, query_id: str, approved_at: datetime) -> bool:
        return (
            await self.session.scalar(
                pg_insert(pre_checkout_approvals)
                .values(
                    order_id=order_id,
                    query_id=query_id,
                    approved_at=approved_at,
                )
                .on_conflict_do_nothing()
                .returning(pre_checkout_approvals.c.order_id)
            )
            is not None
        )

    async def extend_reservation(self, order: Order, expires_at: datetime) -> bool:
        await self.session.execute(
            update(orders)
            .where(orders.c.id == order.id)
            .values(
                expires_at=expires_at,
            )
        )
        if order.unit_id is None:
            return True
        return (
            await self.session.scalar(
                update(inventory_units)
                .where(
                    inventory_units.c.id == order.unit_id,
                    inventory_units.c.status == InventoryState.RESERVED,
                    inventory_units.c.reservation_item_id == order.item_id,
                    inventory_units.c.reservation_expires_at == order.expires_at,
                )
                .values(reservation_expires_at=expires_at)
                .returning(inventory_units.c.id)
            )
            is not None
        )

    async def close(self, order: Order, reason: CloseReason) -> None:
        await self.session.execute(
            update(orders)
            .where(orders.c.id == order.id)
            .values(
                status=OrderStatus.EXPIRED,
                closed_at=func.clock_timestamp(),
                close_reason=reason,
            )
        )
        await self.session.execute(
            update(invoice_intents)
            .where(
                invoice_intents.c.order_id == order.id,
                invoice_intents.c.status == InvoiceStatus.READY,
            )
            .values(status=InvoiceStatus.CANCELLED)
        )

    async def release(self, order: Order) -> bool:
        return (
            await self.session.scalar(
                update(inventory_units)
                .where(
                    inventory_units.c.id == order.unit_id,
                    inventory_units.c.status == InventoryState.RESERVED,
                    inventory_units.c.reservation_item_id == order.item_id,
                )
                .values(
                    status=InventoryState.AVAILABLE,
                    reservation_item_id=None,
                    reservation_expires_at=None,
                )
                .returning(inventory_units.c.id)
            )
            is not None
        )

    async def due_orders(self, limit: int) -> tuple[Order, ...]:
        rows = (
            (
                await self.session.execute(
                    order_query()
                    .where(
                        orders.c.status == OrderStatus.AWAITING_PAYMENT,
                        orders.c.expires_at <= func.clock_timestamp(),
                    )
                    .order_by(orders.c.id)
                    .limit(limit)
                    .with_for_update(of=orders, skip_locked=True)
                )
            )
            .mappings()
            .all()
        )
        return tuple(order_from_row(row) for row in rows)

    async def stale_invoices(self, limit: int) -> tuple[Order, ...]:
        rows = (
            (
                await self.session.execute(
                    order_query()
                    .where(
                        invoice_intents.c.status == InvoiceStatus.SENDING,
                        invoice_intents.c.started_at <= func.clock_timestamp() - INVOICE_CLAIM_TTL,
                    )
                    .order_by(orders.c.id)
                    .limit(limit)
                    .with_for_update(of=orders, skip_locked=True)
                )
            )
            .mappings()
            .all()
        )
        return tuple(order_from_row(row) for row in rows)

    async def claim_invoice(self, order: Order, attempt_id: UUID) -> None:
        await self.session.execute(
            update(invoice_intents)
            .where(
                invoice_intents.c.order_id == order.id,
                invoice_intents.c.status == InvoiceStatus.READY,
            )
            .values(
                status=InvoiceStatus.SENDING,
                attempt_id=attempt_id,
                started_at=func.clock_timestamp(),
            )
        )

    async def finish_invoice(self, order: Order, result: InvoiceDelivery) -> None:
        await self.session.execute(
            update(invoice_intents)
            .where(
                invoice_intents.c.order_id == order.id,
                invoice_intents.c.attempt_id == order.attempt_id,
                invoice_intents.c.status.in_((InvoiceStatus.SENDING, InvoiceStatus.UNCERTAIN)),
            )
            .values(
                status=result.status,
                message_id=result.message_id,
                completed_at=func.clock_timestamp(),
            )
        )

    async def audit(self, order: Order, action: str) -> None:
        await self.session.execute(
            insert(audit_logs).values(
                actor_id=order.buyer_id,
                action=action,
                target_id=order.id,
                correlation_id=uuid4(),
                changes={"product_id": str(order.product_id)},
            )
        )
