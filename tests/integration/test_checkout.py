from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.checkout import CheckoutService
from telebotshop.domain.errors import AccessDenied, OutOfStock
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.orders import InvoiceDelivery, InvoiceStatus, OrderStatus
from telebotshop.domain.models.support import PublicTerms
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.orders import setup_orders
from tests.resources import resource_product


@pytest.mark.asyncio
async def test_checkout_reservation_consent_and_invoice_are_durable_and_replay_safe(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine)
    calls = []

    class Sender:
        async def send(self, request):
            assert engine.pool.checkedout() == 0
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM audit_logs WHERE action='checkout.terms'")
                    )
                    == 1
                )
                assert (
                    await connection.scalar(text("SELECT status FROM invoice_intents")) == "SENDING"
                )
            calls.append(request)
            return InvoiceDelivery(InvoiceStatus.SENT, 123)

    factory = partial(unit_of_work, engine)
    catalog = CatalogService(factory)
    assert (await catalog.availability(101, product.id)).available == 1
    service = CheckoutService(factory, Sender(), PublicTerms("FAKE approved public terms"))
    action = uuid4()
    order = await service.prepare(101, product.id, action)
    assert (await service.prepare(101, product.id, action)).id == order.id
    assert (await catalog.availability(101, product.id)).available == 0
    assert not (await catalog.availability(101, product.id)).ready
    with pytest.raises(OutOfStock):
        await service.prepare(102, product.id, uuid4())
    with pytest.raises(AccessDenied):
        await service.pay(102, order.id, service.terms_version)
    paid = await service.pay(101, order.id, service.terms_version)
    assert paid.status == OrderStatus.AWAITING_PAYMENT
    assert paid.invoice_status == InvoiceStatus.SENT
    assert (await service.pay(101, order.id, service.terms_version)).id == order.id
    assert len(calls) == 1
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == 1
        digest = await connection.scalar(
            text("SELECT changes->>'terms_sha256' FROM audit_logs WHERE action='checkout.terms'")
        )
        assert len(digest) == 64 and digest.startswith(service.terms_version)


@pytest.mark.asyncio
async def test_checkout_cancel_releases_only_owned_unpaid_reservation(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    service = CheckoutService(partial(unit_of_work, engine), None, PublicTerms("FAKE terms"))
    order = await service.prepare(101, product.id, uuid4())
    with pytest.raises(AccessDenied):
        await service.orders.cancel(102, order.id)
    cancelled = await service.orders.cancel(101, order.id)
    assert cancelled.status == OrderStatus.EXPIRED
    assert (
        await CatalogService(partial(unit_of_work, engine)).availability(101, product.id)
    ).available == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,document",
    [
        (ProductType.DIGITAL_FILE, False),
        (ProductType.REUSABLE_RESOURCE, False),
        (ProductType.REUSABLE_RESOURCE, True),
        (ProductType.MANUAL_FULFILLMENT, False),
    ],
)
async def test_all_nonfinite_source_shapes_send_real_invoice_intent_without_finite_reservation(
    migrated_database, kind, document
):
    engine = migrated_database
    product, version = await resource_product(engine, kind, document=document)
    calls = []

    class Sender:
        async def send(self, request):
            assert engine.pool.checkedout() == 0
            calls.append(request)
            return InvoiceDelivery(InvoiceStatus.SENT, 321)

    factory = partial(unit_of_work, engine)
    availability = await CatalogService(factory).availability(101, product.id)
    assert availability.available is None and availability.ready
    service = CheckoutService(factory, Sender(), PublicTerms("FAKE terms"))
    order = await service.prepare(101, product.id, uuid4())
    result = await service.pay(101, order.id, service.terms_version)
    assert result.invoice_status == InvoiceStatus.SENT
    assert len(calls) == 1 and calls[0].order.unit_id is None
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT resource_id FROM order_resources")) == version.id
        )
        assert await connection.scalar(text("SELECT count(*) FROM inventory_units")) == 0
