from functools import partial

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, RefundStarPayment, SendInvoice
from sqlalchemy import text

from telebotshop.bootstrap import runtime_dispatcher
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.support import PublicTerms
from telebotshop.infrastructure.config import PaymentV2Settings
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.integration.test_core_shop import product_fixture
from tests.integration.test_wallet_v2_checkout import fund_and_price
from tests.inventory import fake_cipher, inventory_service
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
async def test_telegram_coupon_management_preview_and_atomic_credit_buy(
    migrated_database, monkeypatch
):
    engine = migrated_database
    product = await product_fixture(engine, ProductType.REUSABLE_RESOURCE, False)
    wallet, _ = await fund_and_price(engine, product, amount=3000)
    telegram = TelegramRecorder(monkeypatch)
    edited = {}

    async def send(bot, method, timeout=None):
        assert engine.pool.checkedout() == 0
        assert not isinstance(method, SendInvoice | RefundStarPayment)
        if isinstance(method, EditMessageText):
            key = (method.chat_id, method.message_id)
            content = (method.text, method.reply_markup)
            if edited.get(key) == content:
                raise TelegramBadRequest(method, "Bad Request: message is not modified")
            edited[key] = content
        return await telegram.send(bot, method, timeout)

    telegram.bot.session.make_request.side_effect = send
    settings = PaymentV2Settings(
        app_env="test",
        database_url="postgresql+asyncpg://fake:fake@127.0.0.1/telebotshop_payment_v2_sandbox",
    )
    dispatcher = runtime_dispatcher(
        partial(unit_of_work, engine),
        inventory_service(engine),
        fake_cipher(),
        telegram.bot,
        PublicTerms("FAKE sandbox terms only"),
        stars_enabled=False,
        payment_v2=settings,
    )
    clock = {"value": 0}
    monkeypatch.setattr(
        "telebotshop.infrastructure.telegram.guard.monotonic", lambda: clock["value"]
    )
    sequence = 100

    async def feed(update):
        nonlocal sequence
        sequence += 1
        clock["value"] += 11
        update = update.model_copy(update={"update_id": sequence})
        if update.message:
            update = update.model_copy(
                update={"message": update.message.model_copy(update={"message_id": sequence})}
            )
        await dispatcher.feed_update(telegram.bot, update)

    def view():
        return next(call for call in reversed(telegram.calls) if getattr(call, "text", None))

    def button(prefix):
        return next(
            button.callback_data
            for row in view().reply_markup.inline_keyboard
            for button in row
            if (button.callback_data or "").startswith(prefix)
        )

    from datetime import UTC, datetime, timedelta

    expiry = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    try:
        await feed(message_update(f"/coupon_create FAKE10 PERCENT 10 {expiry} 1 1 ALL", actor=9001))
        confirm = button("cp:a:")
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM coupons")) == 0
        await feed(callback_update(confirm, actor=101))
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM coupons")) == 0
        await feed(callback_update(confirm, actor=9001))
        await feed(callback_update(confirm, actor=9001))
        await feed(callback_update("cp:d:FAKE10", actor=9001))
        assert "Phạm vi: ALL" in view().text and "Tổng lượt: 1" in view().text
        for value in ("off", "on"):
            await feed(message_update(f"/coupon_state FAKE10 {value}", actor=9001))
            toggle = button("cp:a:")
            await feed(callback_update(toggle, actor=9001))
            async with engine.connect() as connection:
                assert await connection.scalar(text("SELECT active FROM coupons")) is (
                    value == "on"
                )
        await feed(message_update("/coupon DEMO-1 fake10"))
        assert all(amount in view().text for amount in ("1,000", "100", "900"))
        buy = button("wc:b:")
        await feed(callback_update(buy, actor=102))
        assert (await wallet.balance(101)).available == 3000
        await feed(callback_update(buy, actor=101))
        await feed(callback_update(buy, actor=101))
        assert (await wallet.balance(101)).available == 2100
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM coupon_redemptions")) == 1
            assert (
                await connection.scalar(text("SELECT count(*) FROM orders WHERE status='PAID'"))
                == 1
            )
            assert await connection.scalar(text("SELECT amount FROM payments")) == 900
            assert await connection.scalar(text("SELECT count(*) FROM pre_checkout_approvals")) == 0
        acknowledgements = [
            call for call in telegram.calls if isinstance(call, AnswerCallbackQuery)
        ]
        assert len(acknowledgements) == 9
    finally:
        await telegram.bot.session.close()
