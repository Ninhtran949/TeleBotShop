import hashlib
from dataclasses import asdict, fields
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.payments import PaymentRepository
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.orders import Order, OrderAttention, OrderStatus
from telebotshop.domain.models.payments import (
    CommittedWork,
    PaymentReceipt,
    PaymentStatus,
    ReviewReason,
    StoredPayment,
)
from telebotshop.infrastructure.database.models.inventory import inventory_units
from telebotshop.infrastructure.database.models.orders import orders
from telebotshop.infrastructure.database.models.payments import (
    fulfillments,
    payment_conflicts,
    payments,
)


def stored_payment(row) -> StoredPayment:
    return StoredPayment(
        row["id"],
        PaymentReceipt(**{f.name: row[f.name] for f in fields(PaymentReceipt)}),
        PaymentStatus(row["status"]),
        row["order_id"],
        ReviewReason(row["review_reason"]) if row["review_reason"] else None,
    )


class SqlPaymentRepository(PaymentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_charge(self, charge_id: str) -> None:
        digest = hashlib.sha256(("telebotshop.payment:" + charge_id).encode()).digest()
        key = int.from_bytes(digest[:8], "big", signed=True)
        await self.session.execute(select(func.pg_advisory_xact_lock(key)))

    async def by_charge(self, charge_id: str) -> StoredPayment | None:
        row = (
            (
                await self.session.execute(
                    select(payments).where(
                        payments.c.charge_id == charge_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return stored_payment(row)

    async def save(self, payment: StoredPayment) -> None:
        await self.session.execute(
            insert(payments).values(
                id=payment.id,
                **payment.receipt.record(),
                status=payment.status,
                order_id=payment.order_id,
                review_reason=payment.review_reason,
            )
        )

    async def observe_conflict(self, payment_id: UUID, receipt: PaymentReceipt) -> None:
        await self.session.execute(
            pg_insert(payment_conflicts)
            .values(
                id=uuid4(),
                payment_id=payment_id,
                digest=receipt.digest(),
                evidence=receipt.record(),
            )
            .on_conflict_do_nothing(constraint="uq_payment_conflict")
        )

    async def primary(self, order_id: UUID) -> UUID | None:
        return await self.session.scalar(
            select(payments.c.id).where(
                payments.c.order_id == order_id,
                payments.c.status == PaymentStatus.MATCHED,
            )
        )

    async def mark_paid(self, order: Order, attention: OrderAttention | None) -> None:
        result = await self.session.execute(
            update(orders)
            .where(
                orders.c.id == order.id,
                orders.c.status.in_([OrderStatus.AWAITING_PAYMENT, OrderStatus.EXPIRED]),
            )
            .values(status=OrderStatus.PAID, attention_reason=attention)
        )
        if result.rowcount != 1:
            raise InvalidAction("Payment state changed")

    async def allocate(self, order: Order) -> None:
        result = await self.session.execute(
            update(inventory_units)
            .where(
                inventory_units.c.id == order.unit_id,
                inventory_units.c.product_id == order.product_id,
                inventory_units.c.status == "RESERVED",
                inventory_units.c.reservation_item_id == order.item_id,
                inventory_units.c.reservation_expires_at == order.expires_at,
            )
            .values(
                status="ALLOCATED",
                allocated_item_id=order.item_id,
                allocated_at=func.clock_timestamp(),
                reservation_item_id=None,
                reservation_expires_at=None,
            )
        )
        if result.rowcount != 1:
            raise InvalidAction("Payment reservation changed")

    async def save_work(self, work: CommittedWork) -> None:
        values = asdict(work)
        if work.resource_id is None:
            values.pop("resource_id")
        await self.session.execute(insert(fulfillments).values(**values))
