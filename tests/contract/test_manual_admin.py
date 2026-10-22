from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText

from telebotshop.admin.manual import create_manual_admin_router
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.manual import ManualFulfillmentService
from telebotshop.domain.models.fulfillment import DeliveryState
from telebotshop.domain.models.manual import ManualAction, ManualDetail
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
async def test_manual_detail_is_private_plain_text_and_completion_needs_explicit_confirmation(
    monkeypatch,
):
    service, access = AsyncMock(spec=ManualFulfillmentService), AsyncMock(spec=AccessService)
    work_id = uuid4()
    service.detail.return_value = ManualDetail(
        work_id,
        uuid4(),
        101,
        "DEMO-1",
        DeliveryState.AWAITING_MANUAL,
        "Đang chờ.",
        "FAKE private <&> staff link https://example.org",
    )
    action = ManualAction(
        uuid4(), uuid4(), work_id, uuid4(), datetime.now(UTC) + timedelta(minutes=10)
    )
    service.prepare_completion.return_value = action
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_manual_admin_router(service, access))
    try:
        await dispatcher.feed_update(telegram.bot, message_update(f"/manual {work_id}", actor=9001))
        assert "FAKE private <&>" in telegram.calls[-1].text
        assert telegram.calls[-1].parse_mode is None
        assert telegram.calls[-1].link_preview_options.is_disabled is True
        await dispatcher.feed_update(
            telegram.bot, message_update(f"/manual_complete {work_id}\nFAKE evidence", actor=9001)
        )
        service.prepare_completion.assert_awaited_once_with(9001, work_id, "FAKE evidence")
        service.confirm.assert_not_awaited()
        assert "FAKE evidence" not in telegram.calls[-1].text
        data = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
        assert data == f"mf:confirm:{action.id}" and len(data.encode()) <= 64
        await dispatcher.feed_update(telegram.bot, callback_update(data, actor=9001))
        service.confirm.assert_awaited_once_with(9001, action.id)
        assert any(isinstance(call, AnswerCallbackQuery) for call in telegram.calls)
        assert isinstance(telegram.calls[-1], EditMessageText)
    finally:
        await telegram.bot.session.close()
