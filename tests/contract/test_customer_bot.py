from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Update

from telebotshop.application.services.identity import IdentityService
from telebotshop.bot.routers.identity import create_customer_router
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
async def test_start_uses_server_identity_and_only_renders_working_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = AsyncMock(spec=IdentityService)
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.message.outer_middleware(IngressGuard())
    dispatcher.include_router(create_customer_router(identity))
    await dispatcher.feed_update(telegram.bot, message_update("/start"))
    customer = identity.get_or_create_customer.call_args.args[0]
    assert customer.telegram_id == 101
    assert customer.username == "fake_user"
    assert customer.language == "vi"
    reply = telegram.calls[-1]
    assert isinstance(reply, SendMessage)
    assert reply.chat_id == 101
    assert reply.parse_mode is None
    callbacks = {
        button.callback_data for row in reply.reply_markup.inline_keyboard for button in row
    }
    assert callbacks == {"profile", "home", "cancel", "shop:root", "shop:searchhelp"}
    await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        message_update("/start", chat_id=-5, chat_type="group"),
        message_update("/start", chat_id=102),
        callback_update("profile", chat_id=-5, chat_type="supergroup"),
        callback_update("profile", inaccessible=True),
        callback_update("profile", inline=True),
    ],
)
async def test_private_chat_guards_do_not_call_identity(
    monkeypatch: pytest.MonkeyPatch,
    update: Update,
) -> None:
    identity = AsyncMock(spec=IdentityService)
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_customer_router(identity))
    await dispatcher.feed_update(telegram.bot, update)
    assert not identity.get_or_create_customer.called
    assert not identity.get_my_profile.called
    assert all(isinstance(call, AnswerCallbackQuery) for call in telegram.calls)
    await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_callback_home_edits_existing_message_and_cancel_clears_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.callback_query.outer_middleware(IngressGuard())
    dispatcher.include_router(create_customer_router(AsyncMock(spec=IdentityService)))
    await dispatcher.feed_update(telegram.bot, callback_update("home"))
    assert any(isinstance(call, EditMessageText) for call in telegram.calls)
    await dispatcher.feed_update(telegram.bot, callback_update("cancel"))
    last_edit = next(call for call in reversed(telegram.calls) if isinstance(call, EditMessageText))
    assert "Đã hủy" in last_edit.text
    assert last_edit.reply_markup.inline_keyboard[0][0].callback_data == "home"
    await telegram.bot.session.close()
