from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendPhoto

from telebotshop.application.services.topups import TopUpService
from telebotshop.application.services.wallet import WalletLedgerService
from telebotshop.application.services.wallet_checkout import WalletCheckoutService
from telebotshop.bot.routers.wallet import create_wallet_router
from telebotshop.domain.models.topups import BankAccount, TopUpIntent, TopUpState
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
@pytest.mark.parametrize("photo_rejected", [False, True])
async def test_bank_topup_displays_qr_with_text_fallback_and_disabled_rail_hidden(
    monkeypatch, photo_rejected
):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    intent = TopUpIntent(
        id=uuid4(),
        buyer_id=uuid4(),
        action_id=uuid4(),
        rail="BANK_TRANSFER",
        reference="TBFAKE",
        recipient="FAKEBANK:FAKE-ACCOUNT",
        asset="VND",
        amount=Decimal(1000),
        credit_amount=1000,
        network="",
        token_contract="",
        route_id=None,
        rate=Decimal(1),
        rate_source="VND_1_TO_1",
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        state=TopUpState.DETECTED,
    )
    topups = AsyncMock(spec=TopUpService)
    topups.create_bank.return_value = intent
    telegram = TelegramRecorder(monkeypatch)
    photos = []

    async def send(bot, method, timeout=None):
        if isinstance(method, SendPhoto):
            photos.append(method)
            if photo_rejected:
                raise TelegramBadRequest(method=method, message="FAKE provider detail")
        return await telegram.send(bot, method, timeout)

    monkeypatch.setattr(telegram.bot.session, "make_request", AsyncMock(side_effect=send))
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.message.outer_middleware(IngressGuard())
    dispatcher.include_router(
        create_wallet_router(
            AsyncMock(spec=WalletLedgerService),
            AsyncMock(spec=WalletCheckoutService),
            topups,
            lambda _: "https://qr.sepay.vn/img?FAKE",
            usdt_enabled=False,
        )
    )
    try:
        await dispatcher.feed_update(telegram.bot, message_update("/topup bank 1000"))
        assert len(photos) == 1
        assert "TBFAKE" in photos[0].caption and "1,000" in photos[0].caption
        reply = telegram.calls[-1]
        buttons = [b.callback_data for row in reply.reply_markup.inline_keyboard for b in row]
        assert "wal:usdt" not in buttons
        if photo_rejected:
            assert "TBFAKE" in reply.text and "FAKE-ACCOUNT" in reply.text
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_disabled_bank_service_rejects_direct_call_before_database():
    def no_database():
        raise AssertionError("A disabled rail must stop before database work")

    from telebotshop.domain.errors import InvalidAction

    service = TopUpService(
        no_database, enabled=True, bank=BankAccount("FAKE", "FAKE"), bank_enabled=False
    )
    with pytest.raises(InvalidAction):
        await service.create_bank(101, 1000, uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("navigation", ["wal:bank", "wal:usdt"])
@pytest.mark.parametrize("unchanged", [True, False])
async def test_repeated_wallet_navigation_acknowledges_even_when_edit_is_rejected(
    monkeypatch, navigation, unchanged
):
    telegram = TelegramRecorder(monkeypatch)
    edits = []
    error = (
        "message is not modified: FAKE unchanged content"
        if unchanged
        else "message can't be edited"
    )

    async def send(bot, method, timeout=None):
        if isinstance(method, EditMessageText):
            edits.append(method)
            if len(edits) == 2:
                raise TelegramBadRequest(method=method, message=error)
        return await telegram.send(bot, method, timeout)

    telegram.bot.session.make_request.side_effect = send
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(
        create_wallet_router(
            AsyncMock(spec=WalletLedgerService),
            AsyncMock(spec=WalletCheckoutService),
            AsyncMock(spec=TopUpService),
        )
    )
    try:
        await dispatcher.feed_update(telegram.bot, callback_update(navigation))
        if unchanged:
            await dispatcher.feed_update(telegram.bot, callback_update(navigation))
        else:
            with pytest.raises(TelegramBadRequest, match="message can't be edited"):
                await dispatcher.feed_update(telegram.bot, callback_update(navigation))
        assert len(edits) == 2
        answers = [call for call in telegram.calls if isinstance(call, AnswerCallbackQuery)]
        assert len(answers) == 2
    finally:
        await telegram.bot.session.close()
