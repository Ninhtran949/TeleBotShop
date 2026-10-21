from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Dispatcher
from aiogram.methods import SendMessage

from telebotshop.admin.catalog import create_catalog_admin_router
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.domain.errors import AccessDenied
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.catalog_action import CatalogAction, CatalogActionKind
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.catalog import fake_product
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
async def test_product_form_prepares_a_confirmation_from_server_actor_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, access = AsyncMock(spec=CatalogService), AsyncMock(spec=AccessService)
    action = CatalogAction(
        uuid4(),
        uuid4(),
        CatalogActionKind.CREATE,
        uuid4(),
        fake_product(),
        0,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    catalog.prepare_product.return_value = action
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.message.outer_middleware(IngressGuard())
    dispatcher.include_router(create_catalog_admin_router(catalog, access))
    await dispatcher.feed_update(
        telegram.bot,
        message_update(
            "/product DEMO-1 | demo | UNIQUE_KEY | 100\nSản phẩm giả\nMô tả công khai", actor=9001
        ),
    )
    actor, draft = catalog.prepare_product.call_args.args
    assert actor == 9001 and draft.product_type == ProductType.UNIQUE_KEY
    assert draft.price_stars == 100 and draft.sku == "DEMO-1"
    assert not catalog.confirm.called
    reply = telegram.calls[-1]
    assert isinstance(reply, SendMessage)
    assert reply.reply_markup.inline_keyboard[0][0].callback_data == f"pc:confirm:{action.id}"
    await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        message_update("/price DEMO-1 1"),
        callback_update("pc:sale:DEMO-1:ACTIVE"),
        callback_update("pc:confirm:" + str(uuid4())),
    ],
)
async def test_catalog_commands_and_forged_callbacks_require_current_permission(
    monkeypatch: pytest.MonkeyPatch,
    update,
) -> None:
    catalog, access = AsyncMock(spec=CatalogService), AsyncMock(spec=AccessService)
    access.authorize.side_effect = AccessDenied("Access denied")
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_catalog_admin_router(catalog, access))
    await dispatcher.feed_update(telegram.bot, update)
    assert not catalog.prepare_price.called and not catalog.prepare_sale.called
    assert not catalog.confirm.called
    assert "không có quyền" in telegram.calls[-1].text
    await telegram.bot.session.close()
