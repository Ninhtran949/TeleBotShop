from functools import partial
from uuid import uuid4

import httpx2
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.bootstrap import create_dispatcher
from telebotshop.infrastructure.database.uow import SqlAlchemyUnitOfWork, unit_of_work
from tests.inventory import inventory_service
from tests.manual import manual_service
from tests.orders import order_service, setup_orders
from tests.payments import payment_service
from tests.pre_checkout import payable_invoice
from tests.resources import resource_service
from tests.telegram import TelegramRecorder, payment_update, pre_checkout_update


@pytest.mark.asyncio
async def test_receipt_commit_failure_returns_http_error_and_redelivery_commits_once(
    migrated_database,
    monkeypatch,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    factory = partial(unit_of_work, engine)
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = create_dispatcher(
        IdentityService(factory),
        AccessService(factory),
        CatalogService(factory),
        inventory_service(engine),
        PreCheckoutService(factory),
        payment_service(engine),
        resources=resource_service(engine),
        manual=manual_service(engine),
    )
    # ASGI acknowledgement harness only; production webhook authentication belongs to T14.
    app = FastAPI()

    @app.post("/receipt-test")
    async def accept(request: Request):
        await dispatcher.feed_raw_update(telegram.bot, await request.json())
        return {"ok": True}

    update = payment_update(payload=order.payload).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)

    async def failed_commit(self):
        raise OperationalError("fake", {}, OSError("FAKE-private-DSN"))

    try:
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            with monkeypatch.context() as patch:
                patch.setattr(SqlAlchemyUnitOfWork, "commit", failed_commit)
                failed = await client.post("/receipt-test", json=update)
            assert failed.status_code == 500 and "FAKE-private-DSN" not in failed.text
            async with engine.connect() as connection:
                assert await connection.scalar(text("SELECT count(*) FROM payments")) == 0
                assert (
                    await connection.scalar(text("SELECT status FROM inventory_units"))
                    == "RESERVED"
                )
            assert (await client.post("/receipt-test", json=update)).status_code == 200
            assert (await client.post("/receipt-test", json=update)).status_code == 200
        assert not telegram.calls and engine.pool.checkedout() == 0
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM payments")) == 1
            assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_real_dispatcher_approval_then_receipt_uses_server_snapshots_without_delivery(
    migrated_database,
    monkeypatch,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payable_invoice(engine, order)
    factory = partial(unit_of_work, engine)
    telegram = TelegramRecorder(monkeypatch)

    async def send(bot, method, timeout=None):
        assert engine.pool.checkedout() == 0
        return await telegram.send(bot, method, timeout)

    telegram.bot.session.make_request.side_effect = send
    dispatcher = create_dispatcher(
        IdentityService(factory),
        AccessService(factory),
        CatalogService(factory),
        inventory_service(engine),
        PreCheckoutService(factory),
        payment_service(engine),
        resources=resource_service(engine),
        manual=manual_service(engine),
    )
    try:
        await dispatcher.feed_update(telegram.bot, pre_checkout_update(payload=order.payload))
        assert len(telegram.calls) == 1 and telegram.calls[0].ok is True
        await dispatcher.feed_update(telegram.bot, payment_update(payload=order.payload))
        assert len(telegram.calls) == 1
        assert (await order_service(engine).get(101, order.id)).status == "PAID"
    finally:
        await telegram.bot.session.close()
