from functools import partial
from uuid import uuid4

from sqlalchemy import text

from telebotshop.application.services.resends import ResendService
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import DeliveryResult, DeliveryState
from telebotshop.domain.models.inventory import FINITE_TYPES
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import fulfillment_service, paid_work
from tests.inventory import fake_cipher
from tests.orders import order_service
from tests.payments import payment_service, receipt
from tests.resources import resource_product


class RecordingSender:
    def __init__(self, engine, result=None):
        self.engine = engine
        self.requests = []
        self.result = result or DeliveryResult(DeliveryState.SENT, message_id=123)

    async def send(self, request):
        assert self.engine.pool.checkedout() == 0
        self.requests.append(request)
        return self.result


def resend_service(engine, sender, *, cipher=None, factory=None):
    return ResendService(factory or partial(unit_of_work, engine), cipher or fake_cipher(), sender)


async def delivered_order(
    engine, kind=ProductType.UNIQUE_KEY, *, document=False, pre_refunds=False
):
    if kind in FINITE_TYPES:
        order, work_id = await paid_work(engine, kind=kind, pre_refunds=pre_refunds)
    else:
        product, _ = await resource_product(engine, kind, document=document)
        order = await order_service(engine).create(101, product.id, uuid4())
        await payment_service(engine, pre_refunds=pre_refunds).record(receipt(order))
        async with engine.connect() as connection:
            work_id = await connection.scalar(text("SELECT id FROM fulfillments"))
    sender = RecordingSender(engine)
    await fulfillment_service(engine, sender).run_once()
    return order, work_id, sender.requests[0]
