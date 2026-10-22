import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Dispatcher
from aiogram.methods import GetUpdates
from sqlalchemy.exc import OperationalError

from telebotshop.bootstrap import create_dispatcher
from telebotshop.domain.models.payments import PaymentReceipt
from telebotshop.infrastructure.telegram.payments import receipt_from_message
from tests.telegram import TelegramRecorder, payment_update


def dispatcher_for(receiver):
    return create_dispatcher(
        Mock(),
        Mock(),
        Mock(),
        Mock(),
        AsyncMock(),
        Mock(record=receiver),
        resources=AsyncMock(),
        manual=AsyncMock(),
        history=AsyncMock(),
        resends=AsyncMock(),
        support=AsyncMock(),
        refunds=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_financial_message_bypasses_ordinary_guard_and_returns_after_commit(monkeypatch):
    telegram = TelegramRecorder(monkeypatch)
    started, commit = asyncio.Event(), asyncio.Event()

    async def record(receipt):
        assert receipt == PaymentReceipt("FAKE-charge-1", None, "XTR", 100, "x" * 32)
        started.set()
        await commit.wait()

    dispatcher = dispatcher_for(record)
    update = payment_update(actor=None)
    update = update.model_copy(
        update={
            "message": update.message.model_copy(
                update={
                    "chat": update.message.chat.model_copy(update={"type": "group"}),
                }
            )
        }
    )
    task = asyncio.create_task(dispatcher.feed_update(telegram.bot, update))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert not task.done() and not telegram.calls
        commit.set()
        await task
        assert not telegram.calls
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await telegram.bot.session.close()


def test_parser_preserves_unsupported_subscription_evidence_without_customer_details():
    update = payment_update(
        is_recurring=True,
        is_first_recurring=True,
        subscription_expiration_date=123456,
        order_info={"name": "FAKE-private-name", "email": "fake@example.invalid"},
    )
    receipt = receipt_from_message(update.message)
    assert receipt.subscription_expiration_date == 123456
    assert receipt.is_recurring and receipt.is_first_recurring and receipt.has_order_info
    assert "FAKE-private-name" not in str(receipt.record())
    assert "fake@example.invalid" not in str(receipt.record())


@pytest.mark.asyncio
async def test_polling_database_failure_stops_before_offset_and_redelivers_on_restart(monkeypatch):
    telegram = TelegramRecorder(monkeypatch)
    offsets = []

    async def fetch(bot, method, timeout=None):
        offsets.append(method.offset)
        assert method.offset is None
        return [payment_update()]

    telegram.bot.session.make_request.side_effect = fetch
    failed = AsyncMock(side_effect=OperationalError("fake", {}, OSError("FAKE-database")))
    updates = dispatcher_for(failed)._listen_updates(telegram.bot)
    try:
        with pytest.raises(OperationalError):
            await anext(updates)
        assert offsets == [None]
        await updates.aclose()
        committed = AsyncMock()
        updates = dispatcher_for(committed)._listen_updates(telegram.bot)
        assert (await anext(updates)).update_id == 4
        assert offsets == [None, None]
        committed.assert_awaited_once()
    finally:
        await updates.aclose()
        await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_receipt_bypasses_full_rate_window(monkeypatch):
    from telebotshop.infrastructure.telegram.guard import IngressGuard
    from telebotshop.infrastructure.telegram.payments import create_payment_router

    telegram = TelegramRecorder(monkeypatch)
    guard = IngressGuard()
    from time import monotonic

    assert all(guard.admit(101, monotonic()) for _ in range(8))
    assert not guard.admit(101, monotonic())
    record = AsyncMock()
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.message.outer_middleware(guard)
    dispatcher.include_router(create_payment_router(record, AsyncMock()))
    try:
        await dispatcher.feed_update(telegram.bot, payment_update())
        record.assert_awaited_once()
        assert not telegram.calls
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_database_failure_propagates_instead_of_acknowledging_or_sending(monkeypatch):
    telegram = TelegramRecorder(monkeypatch)
    record = AsyncMock(side_effect=OperationalError("fake", {}, OSError("FAKE-secret")))
    try:
        with pytest.raises(OperationalError):
            await dispatcher_for(record).feed_update(telegram.bot, payment_update())
        assert not telegram.calls
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_native_polling_does_not_confirm_offset_before_receipt_commit(monkeypatch):
    telegram = TelegramRecorder(monkeypatch)
    committed = False
    offsets = []

    async def record(receipt):
        nonlocal committed
        assert offsets == [None]
        committed = True

    async def fetch(bot, method, timeout=None):
        assert isinstance(method, GetUpdates)
        offsets.append(method.offset)
        if len(offsets) == 1:
            return [payment_update()]
        assert committed and method.offset == 5
        raise asyncio.CancelledError

    telegram.bot.session.make_request.side_effect = fetch
    updates = dispatcher_for(record)._listen_updates(telegram.bot)
    try:
        update = await anext(updates)
        assert committed and update.update_id == 4 and offsets == [None]
        with pytest.raises(asyncio.CancelledError):
            await anext(updates)
        assert offsets == [None, 5]
    finally:
        await updates.aclose()
        await telegram.bot.session.close()
