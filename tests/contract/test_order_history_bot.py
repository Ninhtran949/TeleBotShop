from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Dispatcher

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.history import HistoryService
from telebotshop.application.services.resends import ResendService
from telebotshop.bot.routers.orders import create_order_router
from telebotshop.domain.models.catalog import Page, ProductType
from telebotshop.domain.models.fulfillment import DeliveryState
from telebotshop.domain.models.history import OrderView, ResendAction, ResendState
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.telegram import TelegramRecorder, callback_update, message_update


def setup(monkeypatch):
    history, resends, access = (
        AsyncMock(spec=HistoryService),
        AsyncMock(spec=ResendService),
        AsyncMock(spec=AccessService),
    )
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_order_router(history, resends, access))
    return history, resends, telegram, dispatcher


@pytest.mark.asyncio
async def test_history_buttons_and_resend_confirmation_preserve_actor_and_hide_payload(monkeypatch):
    history, resends, telegram, dispatcher = setup(monkeypatch)
    view = OrderView(
        uuid4(),
        "Fake <&> title",
        "DEMO-1",
        ProductType.UNIQUE_KEY,
        100,
        "DELIVERED",
        datetime.now(UTC),
        101,
        uuid4(),
        DeliveryState.SENT,
        False,
    )
    history.list.return_value = Page((view,), 0, True)
    history.detail.return_value = view
    action = ResendAction(
        uuid4(), uuid4(), view.work_id, uuid4(), False, datetime.now(UTC) + timedelta(minutes=10)
    )
    resends.prepare.return_value = action
    resends.confirm.return_value = ResendAction(
        action.id,
        action.actor_id,
        action.work_id,
        action.attempt_id,
        False,
        action.expires_at,
        ResendState.SENT,
    )
    try:
        await dispatcher.feed_update(telegram.bot, message_update("/orders"))
        history.list.assert_awaited_once_with(101, page=0, staff=False)
        assert "Fake <&> title" in str(telegram.calls[-1].reply_markup)
        await dispatcher.feed_update(telegram.bot, callback_update(f"ord:v:{view.id}"))
        assert "100 Stars" in telegram.calls[-1].text
        assert telegram.calls[-1].parse_mode is None
        assert telegram.calls[-1].link_preview_options.is_disabled
        await dispatcher.feed_update(telegram.bot, callback_update(f"ord:r:{view.id}"))
        resends.prepare.assert_awaited_once_with(101, view.id, staff=False)
        resends.confirm.assert_not_awaited()
        assert "cùng" in telegram.calls[-1].text.lower()
        assert (
            telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
            == f"ord:c:{action.id}"
        )
        await dispatcher.feed_update(telegram.bot, callback_update(f"ord:c:{action.id}"))
        resends.confirm.assert_awaited_once_with(101, action.id)
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["ord:l:-1", "ord:v:not-a-uuid", "ord:r:x", "ord:" + "x" * 65])
async def test_history_rejects_malformed_callbacks_before_mutation(monkeypatch, data):
    history, resends, telegram, dispatcher = setup(monkeypatch)
    try:
        await dispatcher.feed_update(telegram.bot, callback_update(data))
        resends.prepare.assert_not_awaited()
        resends.confirm.assert_not_awaited()
        history.detail.assert_not_awaited()
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        message_update("/orders", chat_type="group", chat_id=-5),
        callback_update(f"ord:r:{uuid4()}", chat_id=102),
        callback_update("ord:a:0", inline=True),
    ],
)
async def test_history_cannot_escape_private_actor_chat(monkeypatch, update):
    history, resends, telegram, dispatcher = setup(monkeypatch)
    try:
        await dispatcher.feed_update(telegram.bot, update)
        history.list.assert_not_awaited()
        resends.prepare.assert_not_awaited()
    finally:
        await telegram.bot.session.close()
