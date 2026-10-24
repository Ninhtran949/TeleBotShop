from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendPhoto, TelegramMethod
from aiogram.types import Message

from telebotshop.application.services.catalog import CatalogService
from telebotshop.bot.keyboards import catalog as navigation
from telebotshop.bot.routers.catalog import create_catalog_router
from telebotshop.domain.errors import CatalogNotFound
from telebotshop.domain.models.catalog import Availability, Page, Product, SaleState
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.catalog import fake_product
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
async def test_search_pagination_preserves_query_and_product_uses_public_photo_as_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=CatalogService)
    product = Product(
        uuid4(),
        replace(fake_product(), name="<b>Fake public name</b>"),
        SaleState.ACTIVE,
        0,
        "AgACAg_FAKE_PUBLIC_PHOTO_ID_123456789",
    )
    service.products.return_value = Page((product,), 0, True)
    service.product.return_value = product
    service.availability.return_value = Availability(3, True)
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_catalog_router(service))
    await dispatcher.feed_update(telegram.bot, message_update("/search Bản quyền"))
    reply = telegram.calls[-1]
    next_page = next(
        button.callback_data
        for row in reply.reply_markup.inline_keyboard
        for button in row
        if button.text == "Sau →"
    )
    await dispatcher.feed_update(telegram.bot, callback_update(next_page))
    service.products.assert_awaited_with(101, category_code=None, query="Bản quyền", page=1)
    edit = next(call for call in reversed(telegram.calls) if isinstance(call, EditMessageText))
    detail = next(
        button.callback_data
        for row in edit.reply_markup.inline_keyboard
        for button in row
        if button.callback_data.startswith("shop:p:")
    )
    await dispatcher.feed_update(telegram.bot, callback_update(detail))
    service.product.assert_awaited_once_with(101, product.id)
    text = next(call for call in reversed(telegram.calls) if isinstance(call, EditMessageText))
    assert text.parse_mode is None and text.link_preview_options.is_disabled
    assert "<b>Fake public name</b>" in text.text
    assert any(
        isinstance(call, SendPhoto) and call.photo == product.image_file_id
        for call in telegram.calls
    )
    assert all(
        len(button.callback_data.encode()) <= 64
        for row in text.reply_markup.inline_keyboard
        for button in row
    )
    await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_optional_photo_failure_is_redacted_and_callback_is_already_acknowledged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = AsyncMock(spec=CatalogService)
    product = Product(
        uuid4(), fake_product(), SaleState.ACTIVE, 0, "AgACAg_FAKE_PUBLIC_PHOTO_ID_123456789"
    )
    service.products.return_value = Page((product,), 0, False)
    service.product.return_value = product
    service.availability.return_value = Availability(3, True)
    telegram = TelegramRecorder(monkeypatch)
    photo_attempts = []

    async def send(bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Message | bool:
        if isinstance(method, SendPhoto):
            photo_attempts.append(method)
            raise TelegramBadRequest(method=method, message="FAKE_SECRET_PROVIDER_RESPONSE")
        return await telegram.send(bot, method, timeout)

    monkeypatch.setattr(telegram.bot.session, "make_request", AsyncMock(side_effect=send))
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.callback_query.outer_middleware(IngressGuard())
    dispatcher.include_router(create_catalog_router(service))
    await dispatcher.feed_update(telegram.bot, message_update("/search Fake"))
    detail = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    await dispatcher.feed_update(telegram.bot, callback_update(detail))
    assert len(photo_attempts) == 1
    assert any(isinstance(call, AnswerCallbackQuery) for call in telegram.calls)
    assert "FAKE_SECRET_PROVIDER_RESPONSE" not in caplog.text
    await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", [False, True])
async def test_cross_actor_or_expired_navigation_does_not_query_other_users_search(
    monkeypatch: pytest.MonkeyPatch,
    expiry: bool,
) -> None:
    monkeypatch.setattr(navigation, "monotonic", lambda: 0)
    service = AsyncMock(spec=CatalogService)
    product = Product(uuid4(), fake_product(), SaleState.ACTIVE, 0)
    service.products.return_value = Page((product,), 0, False)
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(create_catalog_router(service))
    await dispatcher.feed_update(telegram.bot, message_update("/search Fake"))
    button = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    if expiry:
        monkeypatch.setattr(navigation, "monotonic", lambda: 600)
    await dispatcher.feed_update(
        telegram.bot, callback_update(button, actor=101 if expiry else 102)
    )
    service.product.assert_not_awaited()
    assert "/shop" in telegram.calls[-1].text
    await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_hidden_or_removed_product_callback_shows_safe_current_state_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=CatalogService)
    product = Product(uuid4(), fake_product(), SaleState.ACTIVE, 0)
    service.products.return_value = Page((product,), 0, False)
    service.product.side_effect = CatalogNotFound("Product unavailable")
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.callback_query.outer_middleware(IngressGuard())
    dispatcher.include_router(create_catalog_router(service))
    await dispatcher.feed_update(telegram.bot, message_update("/search Fake"))
    detail = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    await dispatcher.feed_update(telegram.bot, callback_update(detail))
    assert "không còn hiển thị" in telegram.calls[-1].text
    assert not any(isinstance(call, SendPhoto) for call in telegram.calls)
    await telegram.bot.session.close()
