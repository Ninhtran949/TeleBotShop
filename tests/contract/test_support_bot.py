from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Dispatcher

from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.support import SupportService
from telebotshop.bot.routers.support import create_support_router
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.domain.models.support import TicketKind, TicketState, TicketSummary
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.telegram import TelegramRecorder, callback_update, message_update


def setup(monkeypatch):
    service, identity = AsyncMock(spec=SupportService), AsyncMock(spec=IdentityService)
    service.contact.return_value = None
    service.terms.return_value = (None, False)
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_support_router(service, identity))
    return service, telegram, dispatcher


@pytest.mark.asyncio
async def test_payment_support_and_terms_do_not_invent_owner_policy(monkeypatch):
    service, telegram, dispatcher = setup(monkeypatch)
    try:
        await dispatcher.feed_update(telegram.bot, message_update("/paysupport"))
        assert "Stars" in telegram.calls[-1].text
        assert "OTP" in telegram.calls[-1].text
        assert "sp:new:PAYMENT" in str(telegram.calls[-1].reply_markup)
        await dispatcher.feed_update(telegram.bot, message_update("/terms"))
        assert "chưa" in telegram.calls[-1].text.lower()
        assert "DD/MM/YYYY" not in telegram.calls[-1].text
        service.terms.return_value = ("FAKE TEST TERMS <&>\nNội dung", False)
        await dispatcher.feed_update(telegram.bot, callback_update("sp:terms:0"))
        assert "FAKE TEST TERMS <&>" in telegram.calls[-1].text
        assert telegram.calls[-1].parse_mode is None
        assert telegram.calls[-1].link_preview_options.is_disabled
        service.create.assert_not_awaited()
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_ticket_transport_creates_one_private_request_without_echoing_body(monkeypatch):
    service, telegram, dispatcher = setup(monkeypatch)
    now = datetime.now(UTC)
    ticket = TicketSummary(uuid4(), TicketKind.GENERAL, None, TicketState.OPEN, 1, False, now, now)
    service.create.return_value = ticket
    try:
        await dispatcher.feed_update(
            telegram.bot, message_update("/ticket GENERAL\nFAKE private <&> body")
        )
        args = service.create.await_args
        assert args.args[:3] == (101, TicketKind.GENERAL, "FAKE private <&> body")
        assert args.kwargs == {"order_id": None}
        assert "FAKE private" not in telegram.calls[-1].text
        assert str(ticket.id) in telegram.calls[-1].text
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        message_update("/support", chat_type="group", chat_id=-5),
        callback_update("st:list:0", chat_id=102),
        callback_update("sp:terms:0", inline=True),
    ],
)
async def test_support_stays_in_private_actor_chat(monkeypatch, update):
    service, telegram, dispatcher = setup(monkeypatch)
    try:
        await dispatcher.feed_update(telegram.bot, update)
        service.contact.assert_not_awaited()
        service.list.assert_not_awaited()
        service.create.assert_not_awaited()
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_support_crypto_failure_never_exposes_private_error_or_body(monkeypatch, caplog):
    service, telegram, dispatcher = setup(monkeypatch)
    service.detail.side_effect = InventoryCryptoError("FAKE PRIVATE decrypted body and key")
    try:
        await dispatcher.feed_update(telegram.bot, message_update(f"/ticket {uuid4()}"))
        assert "bảo mật" in telegram.calls[-1].text
        assert "FAKE PRIVATE" not in telegram.calls[-1].text + caplog.text
    finally:
        await telegram.bot.session.close()
