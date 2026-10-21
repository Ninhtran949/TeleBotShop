import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from telebotshop.application.services.fulfillment import FulfillmentService
from telebotshop.domain.models.fulfillment import (
    DeliveryCode,
    DeliveryPolicy,
    DeliveryResult,
    DeliveryState,
)
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.infrastructure.database.uow import SqlAlchemyUnitOfWork
from tests.fulfillment import fulfillment_service, historical_claim, paid_work, timed_uow


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_commit,state,sends", [(1, "READY", 0), (2, "CLAIMED", 0), (3, "SENDING", 1)]
)
async def test_crash_at_each_commit_boundary_keeps_recoverable_work_without_duplicate_send(
    migrated_database,
    monkeypatch,
    failed_commit,
    state,
    sends,
):
    engine = migrated_database
    await paid_work(engine)
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.SENT, message_id=42)
    service = fulfillment_service(engine, sender)
    original = SqlAlchemyUnitOfWork.commit
    count = 0

    async def commit(self):
        nonlocal count
        count += 1
        if count == failed_commit:
            raise OperationalError("fake", {}, OSError("FAKE-private-DSN"))
        await original(self)

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyUnitOfWork, "commit", commit)
        with pytest.raises(OperationalError):
            await service.run_once()
    assert sender.send.await_count == sends
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == state
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
        future = await connection.scalar(text("SELECT clock_timestamp()")) + timedelta(minutes=10)
    recovered = FulfillmentService(
        timed_uow(engine, future), service.cipher, sender, service.policy
    )
    assert await recovered.recover() == int(failed_commit != 1)
    if failed_commit == 3:
        assert await service.run_once() is False
        assert sender.send.await_count == 1
    else:
        assert await recovered.run_once() is True
        assert sender.send.await_count == 1


@pytest.mark.asyncio
async def test_wrong_key_fails_closed_with_redacted_attention_and_no_network(
    migrated_database, caplog
):
    engine = migrated_database
    await paid_work(engine)
    service = fulfillment_service(engine, AsyncMock())
    service.cipher = Mock(open=Mock(side_effect=InventoryCryptoError("FAKE-private-decrypted-key")))
    assert await service.run_once() is True
    assert await service.run_once() is False
    service.sender.send.assert_not_awaited()
    assert "delivery_attention" in caplog.text and "FAKE-private-decrypted-key" not in caplog.text
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT attention_code FROM fulfillments")) == "CRYPTO"
        assert await connection.scalar(text("SELECT status FROM orders")) == "FAILED"
        audit = str((await connection.execute(text("SELECT changes FROM audit_logs"))).all())
        assert "fake-key" not in audit and "FAKE-private" not in audit


@pytest.mark.asyncio
async def test_cancellation_during_send_leaves_sending_then_recovers_uncertain(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    started = asyncio.Event()

    async def send(request):
        started.set()
        await asyncio.Event().wait()

    sender = AsyncMock()
    sender.send.side_effect = send
    service = fulfillment_service(engine, sender)
    task = asyncio.create_task(service.run_once())
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with engine.connect() as connection:
            future = await connection.scalar(text("SELECT clock_timestamp()")) + timedelta(
                minutes=10
            )
        recovered = FulfillmentService(
            timed_uow(engine, future), service.cipher, sender, service.policy
        )
        assert await recovered.recover() == 1
        assert await service.run_once() is False
        sender.send.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_reduced_retry_budget_after_restart_stops_before_decrypt_or_send(migrated_database):
    engine = migrated_database
    _, claim, claimed_at = await historical_claim(engine, sending=True)
    service = fulfillment_service(engine, AsyncMock())
    historical = FulfillmentService(
        timed_uow(engine, claimed_at + timedelta(seconds=2)),
        service.cipher,
        service.sender,
        service.policy,
    )
    await historical.complete(
        claim, DeliveryResult(DeliveryState.RETRYABLE, code=DeliveryCode.RATE_LIMIT, retry_after=1)
    )
    service.policy = DeliveryPolicy(max_attempts=1)
    service.cipher = Mock(wraps=service.cipher)
    next_claim = await service.claim()
    assert await service.prepare(next_claim) is None
    service.cipher.open.assert_not_called()
    service.sender.send.assert_not_awaited()
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT attention_code FROM fulfillments"))
            == "RETRY_EXHAUSTED"
        )


@pytest.mark.asyncio
async def test_lost_result_commit_ack_does_not_resend_an_already_delivered_order(
    migrated_database,
    monkeypatch,
):
    engine = migrated_database
    await paid_work(engine)
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.SENT, message_id=50)
    service = fulfillment_service(engine, sender)
    original, count = SqlAlchemyUnitOfWork.commit, 0

    async def lost_ack(self):
        nonlocal count
        count += 1
        await original(self)
        if count == 3:
            raise OperationalError("fake commit ack", {}, OSError("FAKE-private-DSN"))

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyUnitOfWork, "commit", lost_ack)
        with pytest.raises(OperationalError):
            await service.run_once()
    assert await service.recover() == 0
    assert await service.run_once() is False
    sender.send.assert_awaited_once()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "DELIVERED"
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "SENT"
