from functools import partial
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import EditMessageText, SendPhoto, TelegramMethod
from aiogram.types import Message, Update
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.history import HistoryService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.payments import PaymentService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.bootstrap import create_dispatcher
from telebotshop.domain.models.catalog import SaleState
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.telegram import guard as guard_module
from tests.inventory import inventory_service
from tests.manual import manual_service
from tests.refunds import refund_service
from tests.resends import RecordingSender, resend_service
from tests.resources import resource_service
from tests.support import support_service
from tests.telegram import TelegramRecorder, callback_update, message_update, photo_update


@pytest.mark.asyncio
async def test_admin_customer_photo_price_and_hidden_callbacks_use_real_db_outside_sends(
    migrated_database: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0}
    monkeypatch.setattr(guard_module, "monotonic", lambda: clock["now"])
    factory = partial(unit_of_work, migrated_database)
    identity, access, catalog = (
        IdentityService(factory),
        AccessService(factory),
        CatalogService(factory),
    )
    await identity.bootstrap_owner(9001)
    telegram = TelegramRecorder(monkeypatch)

    async def send(bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Message | bool:
        assert migrated_database.pool.checkedout() == 0
        return await telegram.send(bot, method, timeout)

    monkeypatch.setattr(telegram.bot.session, "make_request", AsyncMock(side_effect=send))
    dispatcher = create_dispatcher(
        identity,
        access,
        catalog,
        inventory_service(migrated_database),
        PreCheckoutService(factory),
        PaymentService(factory),
        resources=resource_service(migrated_database),
        manual=manual_service(migrated_database),
        support=support_service(migrated_database),
        refunds=refund_service(migrated_database),
        history=HistoryService(factory),
        resends=resend_service(migrated_database, RecordingSender(migrated_database)),
    )

    async def mutate(update: Update, *, cancel: bool = False) -> None:
        await dispatcher.feed_update(telegram.bot, update)
        preview = telegram.calls[-1]
        button = preview.reply_markup.inline_keyboard[0][1 if cancel else 0].callback_data
        await dispatcher.feed_update(telegram.bot, callback_update(button, actor=9001))

    await mutate(message_update("/category demo | Danh mục giả", actor=9001))
    await mutate(
        message_update(
            "/product DEMO | demo | UNIQUE_KEY | 100\nTên giả\nMô tả công khai", actor=9001
        )
    )
    assert (await catalog.products(101)).items == ()
    await mutate(message_update("/sale DEMO active", actor=9001))
    await mutate(photo_update("/productimage DEMO"))
    assert (await catalog.admin_product(9001, "DEMO")).image_file_id is not None
    await dispatcher.feed_update(telegram.bot, message_update("/start"))
    await dispatcher.feed_update(telegram.bot, message_update("/shop"))
    category_button = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    await dispatcher.feed_update(telegram.bot, callback_update(category_button))
    category_page = next(
        call for call in reversed(telegram.calls) if isinstance(call, EditMessageText)
    )
    all_button = next(
        button.callback_data
        for row in category_page.reply_markup.inline_keyboard
        for button in row
        if button.callback_data.startswith("shop:a:")
    )
    await dispatcher.feed_update(telegram.bot, callback_update(all_button))
    product_page = next(
        call for call in reversed(telegram.calls) if isinstance(call, EditMessageText)
    )
    detail_button = product_page.reply_markup.inline_keyboard[0][0].callback_data
    await dispatcher.feed_update(telegram.bot, callback_update(detail_button))
    assert any(isinstance(call, SendPhoto) for call in telegram.calls)

    clock["now"] = 11
    await mutate(message_update("/price DEMO 250", actor=9001))
    await dispatcher.feed_update(telegram.bot, callback_update(detail_button))
    detail = next(call for call in reversed(telegram.calls) if isinstance(call, EditMessageText))
    assert "250 Stars" in detail.text and "100 Stars" not in detail.text
    await mutate(message_update("/price DEMO 300", actor=9001), cancel=True)
    assert (await catalog.admin_product(9001, "DEMO")).draft.price_stars == 250
    await dispatcher.feed_update(telegram.bot, callback_update("pc:sale:DEMO:HIDDEN"))
    assert "không có quyền" in telegram.calls[-1].text
    assert (await catalog.admin_product(9001, "DEMO")).sale_state == SaleState.ACTIVE
    await mutate(message_update("/sale DEMO hidden", actor=9001))
    await dispatcher.feed_update(telegram.bot, callback_update(detail_button))
    assert "không còn hiển thị" in telegram.calls[-1].text
    await telegram.bot.session.close()
