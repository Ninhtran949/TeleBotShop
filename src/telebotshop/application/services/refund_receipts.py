from datetime import timedelta
from uuid import uuid4

from telebotshop.application.ports.uow import UnitOfWork
from telebotshop.domain.models.payments import StoredPayment
from telebotshop.domain.models.refunds import RefundIntent, RefundState


async def apply_received_refund(work: UnitOfWork, payment: StoredPayment) -> RefundIntent | None:
    source = await work.refunds.matching_receipt(payment)
    if source is None:
        return None
    intent = await work.refunds.active(payment.id)
    if intent and intent.status == RefundState.SUCCEEDED:
        return intent
    now = await work.orders.now()
    if intent is None:
        intent = RefundIntent(
            uuid4(),
            payment.id,
            None,
            await work.refunds.snapshot(payment.id),
            False,
            now + timedelta(minutes=10),
            RefundState.SUCCEEDED,
            completed_at=now,
            confirmed_by=source,
        )
        await work.refunds.save(intent, None)
    else:
        await work.refunds.finish(intent, RefundState.SUCCEEDED, now, source)
    await work.refunds.hold(intent)
    await work.refunds.mark_refunded(payment)
    return await work.refunds.intent(intent.id)
