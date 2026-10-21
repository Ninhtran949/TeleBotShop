from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Dispatcher
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import Update

from telebotshop.admin.identity import create_admin_router
from telebotshop.application.services.access import AccessService
from telebotshop.domain.errors import AccessDenied
from telebotshop.domain.models.access import Access, Permission
from telebotshop.domain.models.admin import ActionKind, AdminAction
from telebotshop.domain.models.identity import Role
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        message_update("/admin"),
        message_update("/grant 102 ADMIN payments.refund"),
        callback_update("adm:settings"),
        callback_update("adm:confirm:" + str(uuid4())),
    ],
)
async def test_normal_user_and_tampered_admin_callbacks_are_denied_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    update: Update,
) -> None:
    service = AsyncMock(spec=AccessService)
    service.admin_access.side_effect = AccessDenied("Access denied")
    service.authorize.side_effect = AccessDenied("Access denied")
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_admin_router(service))
    await dispatcher.feed_update(telegram.bot, update)
    assert not service.prepare_permission.called
    assert not service.prepare_support.called
    assert not service.confirm.called
    assert any("không có quyền" in call.text for call in telegram.calls)
    await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["confirm", "cancel"])
async def test_grant_command_creates_bound_confirmation_without_applying_it(
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
) -> None:
    service = AsyncMock(spec=AccessService)
    action = AdminAction(
        uuid4(),
        uuid4(),
        ActionKind.GRANT,
        uuid4(),
        Role.ADMIN,
        Permission.REFUND,
        None,
        0,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    service.prepare_permission.return_value = action
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_admin_router(service))
    await dispatcher.feed_update(
        telegram.bot, message_update("/grant 102 ADMIN payments.refund", actor=9001)
    )
    service.prepare_permission.assert_awaited_once_with(
        9001, 102, Role.ADMIN, Permission.REFUND, True
    )
    assert not service.confirm.called
    reply = telegram.calls[-1]
    assert isinstance(reply, SendMessage)
    confirm = reply.reply_markup.inline_keyboard[0][0].callback_data
    assert confirm == "adm:confirm:" + str(action.id)
    assert len(confirm.encode()) <= 64
    callback = f"adm:{verb}:{action.id}"
    await dispatcher.feed_update(telegram.bot, callback_update(callback, actor=9001))
    if verb == "confirm":
        service.confirm.assert_awaited_once_with(9001, action.id)
    else:
        service.cancel.assert_awaited_once_with(9001, action.id)
        service.confirm.assert_not_awaited()
    assert isinstance(telegram.calls[-1], AnswerCallbackQuery)
    await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_staff_menu_shows_only_current_grants_without_mock_dashboard_or_owner_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=AccessService)
    service.admin_access.return_value = Access(
        uuid4(), 101, Role.ADMIN, frozenset({Permission.MANAGE_CATALOG}), 1
    )
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(create_admin_router(service))
    await dispatcher.feed_update(telegram.bot, message_update("/admin"))
    reply = telegram.calls[-1]
    callbacks = {
        button.callback_data for row in reply.reply_markup.inline_keyboard for button in row
    }
    assert not callbacks & {"adm:access", "adm:settings"}
    assert "pc:list:0" in callbacks
    assert "Dashboard" not in reply.text and "0" not in reply.text
    await telegram.bot.session.close()
