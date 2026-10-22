from functools import partial
from uuid import uuid4

import pytest
from aiogram.methods import SendMessage
from sqlalchemy import text

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.bootstrap import create_dispatcher
from telebotshop.domain.models.catalog import ProductType
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.delivery.finite import FiniteDeliverySender
from tests.fulfillment import fulfillment_service
from tests.inventory import inventory_service
from tests.manual import manual_service
from tests.orders import order_service
from tests.payments import payment_service, receipt
from tests.resources import resource_product, resource_service
from tests.telegram import TelegramRecorder, callback_update, document_update, message_update


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
async def test_runtime_resource_delivery_and_manual_completion_close_uow_before_telegram(
    migrated_database, monkeypatch, kind, document
):
    engine = migrated_database
    product, _ = await resource_product(engine, kind, document=document, publish=False)
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
        if kind == ProductType.DIGITAL_FILE or document:
            update = document_update(
                "/resource DEMO-1", file_name="demo.pdf", mime_type="application/pdf"
            )
        else:
            body = (
                "FAKE private staff instructions"
                if kind == ProductType.MANUAL_FULFILLMENT
                else "FAKE reusable content"
            )
            update = message_update(f"/resource DEMO-1\n{body}", actor=9001)
        await dispatcher.feed_update(telegram.bot, update)
        preview = telegram.calls[-1]
        assert "FAKE private" not in preview.text and "FAKE reusable" not in preview.text
        await dispatcher.feed_update(
            telegram.bot,
            callback_update(preview.reply_markup.inline_keyboard[0][0].callback_data, actor=9001),
        )
        order = await order_service(engine).create(101, product.id, uuid4())
        await payment_service(engine).record(receipt(order))
        assert await fulfillment_service(engine, FiniteDeliverySender(telegram.bot)).run_once()
        if kind == ProductType.MANUAL_FULFILLMENT:
            async with engine.connect() as connection:
                work_id = await connection.scalar(text("SELECT id FROM fulfillments"))
                assert await connection.scalar(text("SELECT status FROM orders")) == "FULFILLING"
            await dispatcher.feed_update(
                telegram.bot, message_update(f"/manual {work_id}", actor=101)
            )
            assert (
                "không có quyền" in telegram.calls[-1].text
                and "FAKE private" not in telegram.calls[-1].text
            )
            await dispatcher.feed_update(
                telegram.bot, message_update(f"/manual {work_id}", actor=9001)
            )
            assert "FAKE private staff instructions" in telegram.calls[-1].text
            await dispatcher.feed_update(
                telegram.bot,
                message_update(f"/manual_complete {work_id}\nFAKE evidence", actor=9001),
            )
            assert "FAKE evidence" not in telegram.calls[-1].text
            data = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
            await dispatcher.feed_update(telegram.bot, callback_update(data, actor=9001))
            assert not any(
                "FAKE private" in (call.text or "")
                for call in telegram.calls
                if isinstance(call, SendMessage) and call.chat_id == 101
            )
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT status FROM orders")) == "DELIVERED"
            assert await connection.scalar(text("SELECT state FROM fulfillments")) == "SENT"
    finally:
        await telegram.bot.session.close()
