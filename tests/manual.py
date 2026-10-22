from functools import partial
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import text

from telebotshop.application.services.manual import ManualFulfillmentService
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import DeliveryResult, DeliveryState
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import fulfillment_service
from tests.inventory import fake_cipher
from tests.orders import order_service
from tests.payments import payment_service, receipt
from tests.resources import resource_product


def manual_service(engine):
    return ManualFulfillmentService(partial(unit_of_work, engine), fake_cipher())


async def waiting_manual(engine):
    product, _ = await resource_product(engine, ProductType.MANUAL_FULFILLMENT)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payment_service(engine).record(receipt(order))
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.SENT, message_id=99)
    await fulfillment_service(engine, sender).run_once()
    async with engine.connect() as connection:
        return order, await connection.scalar(text("SELECT id FROM fulfillments"))
