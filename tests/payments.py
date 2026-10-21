from functools import partial

from telebotshop.application.services.payments import PaymentService
from telebotshop.domain.models.payments import PaymentReceipt
from telebotshop.infrastructure.database.uow import unit_of_work


def payment_service(engine):
    return PaymentService(partial(unit_of_work, engine))


def receipt(order, *, charge="FAKE-charge-1", payer=101, amount=None):
    return PaymentReceipt(
        charge, payer, "XTR", order.price_stars if amount is None else amount, order.payload
    )
