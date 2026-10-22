from contextlib import asynccontextmanager
from functools import partial

from telebotshop.application.services.payments import PaymentService
from telebotshop.domain.models.payments import PaymentReceipt
from telebotshop.infrastructure.database.uow import unit_of_work


def payment_service(engine, *, pre_refunds=False):
    if not pre_refunds:
        return PaymentService(partial(unit_of_work, engine))

    class NoRefundReceiptsBeforeT13:
        async def matching_receipt(self, payment):
            return None

    @asynccontextmanager
    async def historical_uow():
        async with unit_of_work(engine) as work:
            # Explicit predecessor seeding: this schema cannot contain T13 receipts yet.
            work.refunds = NoRefundReceiptsBeforeT13()
            yield work

    return PaymentService(historical_uow)


def receipt(order, *, charge="FAKE-charge-1", payer=101, amount=None):
    return PaymentReceipt(
        charge, payer, "XTR", order.price_stars if amount is None else amount, order.payload
    )
