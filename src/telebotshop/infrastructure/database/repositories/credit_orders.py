from uuid import uuid4, uuid5

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from telebotshop.application.ports.credit_orders import CreditOrderRepository
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.orders import PaymentMethod
from telebotshop.infrastructure.database.models.credit_orders import product_credit_prices as prices
from telebotshop.infrastructure.database.models.identity import audit_logs
from telebotshop.infrastructure.database.models.orders import orders
from telebotshop.infrastructure.database.models.payments import fulfillments, payments
from telebotshop.infrastructure.database.models.wallet_refunds import (
    external_refund_records,
    topup_reversals,
)
from telebotshop.infrastructure.database.models.wallet_refunds import (
    wallet_refund_actions as refunds,
)


class SqlCreditOrderRepository(CreditOrderRepository):
    def __init__(self, session):
        self.session = session

    async def price(self, product_id):
        return await self.session.scalar(
            select(prices.c.price_credits).where(prices.c.product_id == product_id)
        )

    async def set_price(self, product_id, amount, actor_id, action_id, reason):
        correlation = uuid5(action_id, "wallet.price")
        existing = (
            (
                await self.session.execute(
                    select(audit_logs).where(audit_logs.c.correlation_id == correlation)
                )
            )
            .mappings()
            .one_or_none()
        )
        changes = {"price_credits": amount, "reason": reason}
        if existing is not None:
            if (existing["actor_id"], existing["target_id"], existing["changes"]) != (
                actor_id,
                product_id,
                changes,
            ):
                raise InvalidAction("Credit price action conflicts")
            return
        await self.session.execute(
            pg_insert(prices)
            .values(product_id=product_id, price_credits=amount)
            .on_conflict_do_update(
                index_elements=[prices.c.product_id], set_={"price_credits": amount}
            )
        )
        await self.session.execute(
            insert(audit_logs).values(
                id=uuid4(),
                actor_id=actor_id,
                action="wallet.price",
                target_id=product_id,
                correlation_id=correlation,
                changes=changes,
            )
        )

    async def pay(self, order, journal_id, telegram_id):
        payment_id = uuid4()
        await self.session.execute(
            insert(payments).values(
                id=payment_id,
                payment_method=PaymentMethod.WALLET_CREDIT,
                wallet_journal_id=journal_id,
                charge_id=None,
                payer_id=telegram_id,
                currency="CRD",
                amount=order.price_credits,
                payload=None,
                provider_charge_id=None,
                is_recurring=False,
                is_first_recurring=False,
                has_order_info=False,
                order_id=order.id,
                status="MATCHED",
            )
        )
        return payment_id

    async def refund_action(self, action_id, *, for_update=False):
        query = select(refunds).where(refunds.c.id == action_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else dict(row)

    async def save_refund(
        self, action_id, order_id, actor_id, reason, source, expected, expires_at
    ):
        await self.session.execute(
            insert(refunds).values(
                id=action_id,
                order_id=order_id,
                actor_id=actor_id,
                reason=reason,
                source_digest=source,
                expected=expected,
                expires_at=expires_at,
            )
        )

    async def apply_refund(self, action_id, journal_id, order_id, work_id):
        await self.session.execute(
            update(refunds).where(refunds.c.id == action_id).values(journal_id=journal_id)
        )
        await self.session.execute(
            update(fulfillments).where(fulfillments.c.id == work_id).values(hold=True)
        )
        await self.session.execute(
            update(orders).where(orders.c.id == order_id).values(status="REFUNDED")
        )

    async def reverse_topup(self, topup_id, journal_id, actor_id, reason, source):
        await self.session.execute(
            pg_insert(topup_reversals)
            .values(
                topup_id=topup_id,
                journal_id=journal_id,
                actor_id=actor_id,
                reason=reason,
                evidence=source,
            )
            .on_conflict_do_nothing()
        )

    async def external_refunds(self, topup_id):
        rows = (
            await self.session.execute(
                select(external_refund_records)
                .where(external_refund_records.c.topup_id == topup_id)
                .order_by(external_refund_records.c.created_at, external_refund_records.c.id)
            )
        ).mappings()
        return tuple(dict(row) for row in rows)

    async def record_external_refund(self, action_id, topup_id, actor_id, state, reason, source):
        await self.session.execute(
            insert(external_refund_records).values(
                id=action_id,
                topup_id=topup_id,
                actor_id=actor_id,
                state=state,
                reason=reason,
                source_digest=source,
            )
        )
