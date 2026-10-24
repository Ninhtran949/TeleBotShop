from functools import partial
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from telebotshop import bootstrap
from telebotshop.application.services.checkout import CheckoutService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.domain.models.support import PublicTerms
from telebotshop.infrastructure.config import WebhookSettings
from telebotshop.infrastructure.database.uow import SqlAlchemyUnitOfWork, unit_of_work
from telebotshop.infrastructure.telegram.invoices import StarsInvoiceSender
from tests.inventory import fake_inventory_settings
from tests.orders import setup_orders
from tests.pre_checkout import checkout_request
from tests.telegram import FAKE_TOKEN, TelegramRecorder, payment_update, refund_update


@pytest.mark.asyncio
async def test_authenticated_webhook_lifespan_receipt_failure_replay_and_refund_are_native(
    migrated_database, monkeypatch, caplog
):
    engine = migrated_database
    product = await setup_orders(engine)
    telegram = TelegramRecorder(monkeypatch)
    close = AsyncMock(wraps=telegram.bot.session.close)
    monkeypatch.setattr(telegram.bot.session, "close", close)
    factory = partial(unit_of_work, engine)
    service = CheckoutService(factory, StarsInvoiceSender(telegram.bot), PublicTerms("FAKE terms"))
    order = await service.prepare(101, product.id, uuid4())
    await service.pay(101, order.id, service.terms_version)
    assert await PreCheckoutService(factory).check(checkout_request(order))
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(bootstrap, "create_bot", lambda settings: telegram.bot)
    settings = WebhookSettings(
        app_env="test",
        database_url=engine.url.render_as_string(hide_password=False),
        bot_token=FAKE_TOKEN,
        owner_id=9001,
        telegram_environment="test",
        webhook_secret="FAKE_webhook_secret_12345678901234567890",
    )
    app = bootstrap.create_webhook_app(settings, fake_inventory_settings())
    headers = {"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret.get_secret_value()}
    real_commit = SqlAlchemyUnitOfWork.commit

    async def failed_commit(self):
        raise SQLAlchemyError("FAKE_SECRET_COMMIT_BODY")

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fake-test"
        ) as client:
            payload = payment_update(payload=order.payload).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            assert (await client.post("/telegram/webhook", json=payload)).status_code == 403
            monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", failed_commit)
            assert (
                await client.post("/telegram/webhook", headers=headers, json=payload)
            ).status_code == 503
            async with engine.connect() as c:
                assert await c.scalar(text("SELECT count(*) FROM payments")) == 0
            monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", real_commit)
            for _ in range(2):
                assert (
                    await client.post("/telegram/webhook", headers=headers, json=payload)
                ).status_code == 200
            refunded = refund_update(payload=order.payload).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            for _ in range(2):
                assert (
                    await client.post("/telegram/webhook", headers=headers, json=refunded)
                ).status_code == 200
            async with engine.connect() as c:
                assert await c.scalar(text("SELECT count(*) FROM payments")) == 1
                assert await c.scalar(text("SELECT status FROM orders")) == "REFUNDED"
                assert await c.scalar(text("SELECT count(*) FROM fulfillments WHERE hold")) == 1
            assert (await client.get("/health/ready")).status_code == 200
    close.assert_awaited_once()
    assert engine.pool.checkedout() == 0 and engine.pool.checkedin() == 0
    assert "FAKE_SECRET_COMMIT_BODY" not in caplog.text
