from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial
from uuid import uuid4

from sqlalchemy import text

from telebotshop.application.services.fulfillment import FulfillmentService
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.models.catalog import LicenseMetadata, ProductType, SaleState
from telebotshop.domain.models.fulfillment import (
    DeliveryAttempt,
    DeliveryClaim,
    DeliveryPolicy,
    DeliveryState,
)
from telebotshop.domain.models.identity import CustomerIdentity
from telebotshop.domain.models.inventory import ImportFormat
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.catalog import fake_product, setup_catalog
from tests.inventory import fake_cipher, inventory_service
from tests.orders import order_service, setup_orders
from tests.payments import payment_service, receipt


def fulfillment_service(engine, sender, *, policy=None):
    return FulfillmentService(
        partial(unit_of_work, engine),
        fake_cipher(),
        sender,
        DeliveryPolicy() if policy is None else policy,
    )


async def paid_work(engine, *, kind=ProductType.UNIQUE_KEY):
    if kind == ProductType.UNIQUE_KEY:
        product = await setup_orders(engine)
    else:
        from dataclasses import replace

        catalog = await setup_catalog(engine)
        draft = replace(
            fake_product(), product_type=kind, metadata=LicenseMetadata("Fake activation")
        )
        action = await catalog.prepare_product(9001, draft)
        await catalog.confirm(9001, action.id)
        action = await catalog.prepare_sale(9001, draft.sku, SaleState.ACTIVE)
        await catalog.confirm(9001, action.id)
        product = await catalog.admin_product(9001, draft.sku)
        inventory = inventory_service(engine)
        imported = await inventory.prepare_import(
            9001, draft.sku, b"FAKE-license-0", ImportFormat.TXT
        )
        await inventory.confirm(9001, imported.id)
        await IdentityService(partial(unit_of_work, engine)).get_or_create_customer(
            CustomerIdentity(101, None, "Fake customer")
        )
    order = await order_service(engine).create(101, product.id, uuid4())
    await payment_service(engine).record(receipt(order))
    async with engine.connect() as connection:
        work_id = await connection.scalar(text("SELECT id FROM fulfillments"))
    return order, work_id


def timed_uow(engine, now):
    @asynccontextmanager
    async def factory():
        async with unit_of_work(engine) as work:

            async def clock():
                return now

            work.orders.now = clock
            yield work

    return factory


async def historical_claim(engine, *, sending=False):
    order, work_id = await paid_work(engine)
    async with unit_of_work(engine) as work:
        fulfillment = await work.fulfillment.get(work_id, for_update=True)
        now = await work.orders.now() - timedelta(minutes=5)
        attempt = DeliveryAttempt(
            uuid4(), work_id, DeliveryState.CLAIMED, now, now + timedelta(seconds=30)
        )
        await work.fulfillment.start_claim(fulfillment, attempt)
        claim = DeliveryClaim(work_id, order.id, attempt.id)
        if sending:
            await work.fulfillment.start_send(
                claim, now + timedelta(seconds=1), now + timedelta(seconds=60)
            )
            await work.fulfillment.set_order_state(order.id, "FULFILLING")
        await work.commit()
    return order, claim, now
