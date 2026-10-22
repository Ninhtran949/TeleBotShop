from functools import partial
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import SendDocument, SendMessage
from sqlalchemy import text

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.history import HistoryService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.payments import PaymentService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.bootstrap import create_dispatcher
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.identity import CustomerIdentity, Role
from telebotshop.domain.models.resources import DigitalFilePayload
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.delivery.finite import FiniteDeliverySender
from tests.inventory import inventory_service
from tests.manual import manual_service
from tests.resends import delivered_order, resend_service
from tests.resources import FILE_ID, resource_service
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [ProductType.UNIQUE_KEY, ProductType.DIGITAL_FILE])
async def test_real_history_resend_routes_are_scoped_and_send_only_to_original_buyer(
    migrated_database, monkeypatch, caplog, kind
):
    engine = migrated_database
    order, _, original = await delivered_order(engine, kind)
    factory = partial(unit_of_work, engine)
    identity, access = IdentityService(factory), AccessService(factory)
    await identity.get_or_create_customer(CustomerIdentity(200, None, "Fake staff"))
    grant = await access.prepare_permission(9001, 200, Role.ADMIN, Permission.VIEW_ORDERS, True)
    await access.confirm(9001, grant.id)
    telegram = TelegramRecorder(monkeypatch)
    clock = {"now": 0}
    monkeypatch.setattr("telebotshop.infrastructure.telegram.guard.monotonic", lambda: clock["now"])

    async def send(bot, method, timeout=None):
        assert engine.pool.checkedout() == 0
        return await telegram.send(bot, method, timeout)

    monkeypatch.setattr(telegram.bot.session, "make_request", AsyncMock(side_effect=send))
    dispatcher = create_dispatcher(
        identity,
        access,
        CatalogService(factory),
        inventory_service(engine),
        PreCheckoutService(factory),
        PaymentService(factory),
        resources=resource_service(engine),
        manual=manual_service(engine),
        history=HistoryService(factory),
        resends=resend_service(engine, FiniteDeliverySender(telegram.bot)),
    )

    async def feed(update):
        clock["now"] += 2
        await dispatcher.feed_update(telegram.bot, update)

    try:
        await feed(message_update("/orders"))
        assert str(order.id) in str(telegram.calls[-1].reply_markup)
        await feed(callback_update(f"ord:v:{order.id}", actor=102))
        assert order.title not in telegram.calls[-1].text
        await feed(message_update(f"/staff_orders {order.id}", actor=200))
        callbacks = str(telegram.calls[-1].reply_markup)
        assert f"ord:t:{order.id}" not in callbacks
        await feed(callback_update(f"ord:t:{order.id}", actor=200))
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM resend_actions")) == 0
        grant = await access.prepare_permission(
            9001, 200, Role.ADMIN, Permission.MANAGE_FULFILLMENT, True
        )
        await access.confirm(9001, grant.id)
        await feed(callback_update(f"ord:t:{order.id}", actor=200))
        action = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
        if kind == ProductType.DIGITAL_FILE:
            resource = resource_service(engine)
            changed = await resource.prepare(
                9001, order.sku, DigitalFilePayload(FILE_ID + "_new", "demo.pdf", "application/pdf")
            )
            await resource.confirm(9001, changed.id)
        await feed(callback_update(action, actor=101))
        await feed(callback_update(action, actor=200))
        await feed(callback_update(action, actor=200))
        if kind == ProductType.UNIQUE_KEY:
            deliveries = [
                call
                for call in telegram.calls
                if isinstance(call, SendMessage) and call.text == original.payload.key
            ]
        else:
            deliveries = [call for call in telegram.calls if isinstance(call, SendDocument)]
        assert len(deliveries) == 1 and deliveries[0].chat_id == 101
        if kind == ProductType.DIGITAL_FILE:
            assert deliveries[0].document == FILE_ID
        await feed(message_update("/dashboard", actor=200))
        assert "Số đơn: 1" in telegram.calls[-1].text
        assert "100 Stars" in telegram.calls[-1].text
        assert "fake-key" not in caplog.text and FILE_ID not in caplog.text
    finally:
        await telegram.bot.session.close()
