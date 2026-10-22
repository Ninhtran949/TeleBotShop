import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError

from telebotshop.infrastructure.telegram.guard import IngressGuard
from telebotshop.infrastructure.telegram.payments import PaymentsDispatcher, create_payment_router
from tests.telegram import TelegramRecorder, refund_update


@pytest.mark.asyncio
async def test_refund_event_bypasses_guard_and_returns_only_after_durable_receipt(monkeypatch):
    telegram = TelegramRecorder(monkeypatch)
    started, commit = asyncio.Event(), asyncio.Event()

    async def record(receipt):
        assert receipt.charge_id == "FAKE-charge-1" and receipt.amount == 100
        started.set()
        await commit.wait()

    dispatcher = PaymentsDispatcher(AsyncMock(), record)
    dispatcher.message.outer_middleware(IngressGuard())
    dispatcher.include_router(create_payment_router(AsyncMock(), record))
    update = refund_update(actor=None)
    update = update.model_copy(
        update={
            "message": update.message.model_copy(
                update={"chat": update.message.chat.model_copy(update={"type": "group"})}
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


@pytest.mark.asyncio
async def test_refund_polling_failure_keeps_offset_for_restart_redelivery(monkeypatch):
    telegram = TelegramRecorder(monkeypatch)
    offsets = []

    async def fetch(bot, method, timeout=None):
        offsets.append(method.offset)
        assert method.offset is None
        return [refund_update()]

    telegram.bot.session.make_request.side_effect = fetch
    failed = AsyncMock(side_effect=OperationalError("fake", {}, OSError("FAKE-private-dsn")))
    updates = PaymentsDispatcher(AsyncMock(), failed)._listen_updates(telegram.bot)
    try:
        with pytest.raises(OperationalError):
            await anext(updates)
        await updates.aclose()
        accepted = AsyncMock()
        updates = PaymentsDispatcher(AsyncMock(), accepted)._listen_updates(telegram.bot)
        assert (await anext(updates)).update_id == 4
        assert offsets == [None, None]
        accepted.assert_awaited_once()
    finally:
        await updates.aclose()
        await telegram.bot.session.close()
