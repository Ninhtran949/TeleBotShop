from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4, uuid5

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from telebotshop.application.ports.refunds import RefundRepository
from telebotshop.domain.models.catalog import Page
from telebotshop.domain.models.inventory import SealedPayload
from telebotshop.domain.models.payments import PaymentStatus
from telebotshop.domain.models.refunds import RefundIntent, RefundSnapshot, RefundState, RefundView
from telebotshop.infrastructure.database.models.identity import audit_logs
from telebotshop.infrastructure.database.models.orders import orders
from telebotshop.infrastructure.database.models.payments import fulfillments, payments
from telebotshop.infrastructure.database.models.refunds import (
    payment_verifications,
    refund_intents,
    refund_receipts,
)
from telebotshop.infrastructure.database.repositories.payments import stored_payment

ACTIVE = ["PENDING", "SENDING", "UNCERTAIN", "SUCCEEDED"]


def intent_from_row(row):
    values = {name: row[name] for name in RefundIntent.__dataclass_fields__}
    values["expected"] = RefundSnapshot(**values["expected"])
    values["status"] = RefundState(values["status"])
    values["observed_state"] = (
        RefundState(values["observed_state"]) if values["observed_state"] else None
    )
    return RefundIntent(**values)


def view_query():
    latest = (
        select(refund_intents.c.id)
        .where(refund_intents.c.payment_id == payments.c.id)
        .order_by(refund_intents.c.created_at.desc(), refund_intents.c.id.desc())
        .limit(1)
        .correlate(payments)
        .scalar_subquery()
    )
    return select(
        payments.c.id,
        payments.c.order_id,
        payments.c.amount,
        payments.c.currency,
        payments.c.status.label("payment_status"),
        payment_verifications.c.payment_id.label("verified"),
        refund_intents.c.status,
        refund_intents.c.id.label("intent_id"),
        func.refund_snapshot(payments.c.id).label("snapshot"),
    ).select_from(
        payments.outerjoin(payment_verifications).outerjoin(
            refund_intents, refund_intents.c.id == latest
        )
    )


def refund_view(row):
    return RefundView(
        row.id,
        row.order_id,
        row.amount,
        row.currency,
        row.payment_status == "MATCHED",
        row.payment_status == "MATCHED" or row.verified is not None,
        RefundState(row.status) if row.status else None,
        row.intent_id,
        RefundSnapshot(**row.snapshot),
    )


class SqlRefundRepository(RefundRepository):
    def __init__(self, session):
        self.session = session

    async def payment(self, payment_id):
        row = (
            (await self.session.execute(select(payments).where(payments.c.id == payment_id)))
            .mappings()
            .one_or_none()
        )
        return stored_payment(row) if row else None

    async def view(self, payment_id):
        row = (
            await self.session.execute(view_query().where(payments.c.id == payment_id))
        ).one_or_none()
        return refund_view(row) if row else None

    async def queue(self, page):
        rows = (
            await self.session.execute(
                view_query()
                .order_by(payments.c.received_at.desc(), payments.c.id.desc())
                .limit(6)
                .offset(page * 5)
            )
        ).all()
        return Page(tuple(refund_view(row) for row in rows[:5]), page, len(rows) > 5)

    async def snapshot(self, payment_id):
        return RefundSnapshot(
            **(await self.session.scalar(select(func.refund_snapshot(payment_id))))
        )

    async def payer(self, payment_id):
        return await self.session.scalar(
            select(func.coalesce(payment_verifications.c.payer_id, payments.c.payer_id))
            .select_from(payments.outerjoin(payment_verifications))
            .where(payments.c.id == payment_id)
        )

    async def verify(self, payment_id, actor_id, evidence):
        await self.session.execute(
            pg_insert(payment_verifications)
            .values(
                payment_id=payment_id,
                actor_id=actor_id,
                payer_id=evidence.payer_id,
                amount=evidence.amount,
                transaction_at=evidence.created_at,
            )
            .on_conflict_do_nothing()
        )

    async def active(self, payment_id):
        row = (
            (
                await self.session.execute(
                    select(refund_intents).where(
                        refund_intents.c.payment_id == payment_id,
                        refund_intents.c.status.in_(ACTIVE),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return intent_from_row(row) if row else None

    async def intent(self, intent_id, *, for_update=False):
        query = select(refund_intents).where(refund_intents.c.id == intent_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return intent_from_row(row) if row else None

    async def save(self, intent, evidence):
        values = asdict(intent)
        values["expected"] = intent.expected.record()
        if evidence is not None:
            values.update(asdict(evidence))
        await self.session.execute(insert(refund_intents).values(**values))

    async def evidence(self, intent_id):
        row = (
            await self.session.execute(
                select(
                    refund_intents.c.key_version,
                    refund_intents.c.nonce,
                    refund_intents.c.ciphertext,
                ).where(refund_intents.c.id == intent_id)
            )
        ).one()
        return SealedPayload(*row)

    async def start(self, intent, now, deadline):
        await self.session.execute(
            update(refund_intents)
            .where(refund_intents.c.id == intent.id)
            .values(status="SENDING", started_at=now, deadline=deadline)
        )

    async def finish(self, intent, state, now, confirmed_by=None):
        values = dict(status=state, completed_at=now, confirmed_by=confirmed_by)
        if (
            state == RefundState.SUCCEEDED
            and intent.started_at is None
            and intent.actor_id is not None
        ):
            values.update(started_at=now, deadline=now + timedelta(seconds=60))
        await self.session.execute(
            update(refund_intents).where(refund_intents.c.id == intent.id).values(**values)
        )

    async def observe(self, intent, result):
        await self.session.execute(
            update(refund_intents)
            .where(refund_intents.c.id == intent.id, refund_intents.c.observed_state.is_(None))
            .values(observed_state=result.state)
        )

    async def hold(self, intent, *, release=False):
        query = update(fulfillments).where(fulfillments.c.payment_id == intent.payment_id)
        if release:
            query = query.where(fulfillments.c.refund_intent_id == intent.id)
        await self.session.execute(
            query.values(hold=not release, refund_intent_id=None if release else intent.id)
        )

    async def mark_refunded(self, payment):
        if payment.status == PaymentStatus.MATCHED:
            await self.session.execute(
                update(orders)
                .where(orders.c.id == payment.order_id, orders.c.status != "REFUNDED")
                .values(status="REFUNDED")
            )

    async def receipt(self, receipt):
        await self.session.execute(
            pg_insert(refund_receipts)
            .values(id=uuid4(), **asdict(receipt), digest=receipt.digest())
            .on_conflict_do_nothing()
        )

    async def matching_receipt(self, payment):
        receipt = payment.receipt
        return await self.session.scalar(
            select(refund_receipts.c.source)
            .where(
                refund_receipts.c.charge_id == receipt.charge_id,
                refund_receipts.c.currency == "XTR",
                refund_receipts.c.currency == receipt.currency,
                refund_receipts.c.amount == receipt.amount,
                refund_receipts.c.payload == receipt.payload,
                (refund_receipts.c.provider_charge_id == "")
                | (refund_receipts.c.provider_charge_id == receipt.provider_charge_id),
            )
            .limit(1)
        )

    async def audit(self, intent, event):
        if intent.actor_id is None:
            return  # System events retain provenance in the immutable receipt ledger.
        await self.audit_operation(
            intent.actor_id, intent.payment_id, event, uuid5(intent.id, event), intent.id
        )

    async def audit_operation(self, actor_id, payment_id, event, correlation_id, refund_id=None):
        await self.session.execute(
            pg_insert(audit_logs)
            .values(
                actor_id=actor_id,
                action=event,
                target_id=payment_id,
                correlation_id=correlation_id,
                changes={"refund_id": str(refund_id)} if refund_id else {},
            )
            .on_conflict_do_nothing(index_elements=["correlation_id"])
        )
