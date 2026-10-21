import secrets
from datetime import timedelta
from functools import partial
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.orders import OrderService
from telebotshop.domain.models.identity import CustomerIdentity
from telebotshop.domain.models.inventory import ImportFormat
from telebotshop.domain.models.orders import RESERVATION_TTL, InvoiceStatus, Order, OrderStatus
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.inventory import inventory_service, setup_inventory


def order_service(database: AsyncEngine) -> OrderService:
    return OrderService(partial(unit_of_work, database))


async def setup_orders(database: AsyncEngine, count: int = 1):
    product = await setup_inventory(database)
    inventory = inventory_service(database)
    pending = await inventory.prepare_import(
        9001, "DEMO-1", "\n".join(f"fake-key-{i}" for i in range(count)).encode(), ImportFormat.TXT
    )
    await inventory.confirm(9001, pending.id)
    identity = IdentityService(partial(unit_of_work, database))
    for buyer in (101, 102):
        await identity.get_or_create_customer(CustomerIdentity(buyer, None, "Fake customer"))
    return product


async def historical_order(database, product, *, age=timedelta(minutes=20), buyer=101):
    """Seed a valid old reservation without changing clocks or disabling DB constraints."""
    async with unit_of_work(database) as work:
        buyer_id = await work.orders.lock_buyer(buyer)
        unit_id = await work.orders.available_unit(product.id)
        assert buyer_id is not None and unit_id is not None
        created = await work.orders.now() - age
        order = Order(
            uuid4(),
            buyer_id,
            uuid4(),
            product.id,
            unit_id,
            product.draft.sku,
            product.draft.product_type,
            product.draft.name,
            product.draft.description,
            product.draft.price_stars,
            1,
            created,
            created + RESERVATION_TTL,
            OrderStatus.AWAITING_PAYMENT,
            InvoiceStatus.READY,
            secrets.token_urlsafe(24),
        )
        await work.orders.save(order, uuid4())
        await work.commit()
        return order
