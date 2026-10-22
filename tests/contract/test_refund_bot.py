from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Dispatcher

from telebotshop.admin.refunds import create_refund_admin_router
from telebotshop.application.services.refunds import RefundService
from telebotshop.domain.models.refunds import RefundIntent, RefundSnapshot, RefundState, RefundView
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.telegram import TelegramRecorder, callback_update, message_update


def setup(monkeypatch):
    service = AsyncMock(spec=RefundService)
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_refund_admin_router(service))
    return service, telegram, dispatcher


@pytest.mark.asyncio
async def test_refund_preview_is_concrete_and_never_echoes_evidence_or_raw_charge(monkeypatch):
    service, telegram, dispatcher = setup(monkeypatch)
    payment_id = uuid4()
    intent = RefundIntent(
        uuid4(),
        payment_id,
        uuid4(),
        RefundSnapshot(),
        True,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    service.prepare.return_value = intent
    service.detail.return_value = RefundView(
        payment_id, uuid4(), 12, "XTR", True, True, RefundState.PENDING, intent.id, RefundSnapshot()
    )
    try:
        await dispatcher.feed_update(
            telegram.bot, message_update(f"/refund_uncertain {payment_id}\nFAKE-private-reference")
        )
        service.prepare.assert_awaited_once_with(
            101, payment_id, "FAKE-private-reference", accept_uncertain=True
        )
        text = telegram.calls[-1].text
        assert str(payment_id) in text and "12 Stars" in text and "đã nhận" in text
        assert "FAKE-private-reference" not in text
        assert telegram.calls[-1].parse_mode is None
        assert telegram.calls[-1].link_preview_options.is_disabled
        service.confirm.assert_not_awaited()
        await dispatcher.feed_update(telegram.bot, callback_update(f"rf:confirm:{intent.id}"))
        service.confirm.assert_awaited_once_with(101, intent.id)
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_refund_transport_refuses_group_and_malformed_targets(monkeypatch):
    service, telegram, dispatcher = setup(monkeypatch)
    try:
        await dispatcher.feed_update(
            telegram.bot,
            message_update(f"/refund {uuid4()}\nFAKE-reference", chat_type="group", chat_id=-5),
        )
        await dispatcher.feed_update(telegram.bot, callback_update("rf:confirm:tampered"))
        service.prepare.assert_not_awaited()
        service.confirm.assert_not_awaited()
    finally:
        await telegram.bot.session.close()
