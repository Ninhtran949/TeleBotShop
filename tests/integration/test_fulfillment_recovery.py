from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text

from telebotshop.application.services.fulfillment import FulfillmentService
from telebotshop.domain.models.fulfillment import (
    DeliveryCode,
    DeliveryPolicy,
    DeliveryResult,
    DeliveryState,
)
from tests.fulfillment import fulfillment_service, historical_claim, paid_work, timed_uow


@pytest.mark.asyncio
async def test_expired_unsent_claim_requeues_and_stale_token_cannot_read_secret(migrated_database):
    engine = migrated_database
    order, claim, _ = await historical_claim(engine)
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.SENT, message_id=88)
    service = fulfillment_service(engine, sender)
    cipher = Mock(wraps=service.cipher)
    service.cipher = cipher
    assert await service.prepare(claim) is None
    cipher.open.assert_not_called()
    assert await service.recover() == 1
    assert await service.prepare(claim) is None
    assert await service.run_once() is True
    sender.send.assert_awaited_once()
    assert sender.send.await_args.args[0].context.unit_id == order.unit_id
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM delivery_attempts")) == 2
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM delivery_attempts WHERE state = 'ABANDONED'")
            )
            == 1
        )
        assert await connection.scalar(text("SELECT send_count FROM fulfillments")) == 1


@pytest.mark.asyncio
async def test_expired_sending_is_uncertain_and_late_success_is_observed_without_resend(
    migrated_database,
    caplog,
):
    engine = migrated_database
    _, claim, _ = await historical_claim(engine, sending=True)
    sender = AsyncMock()
    service = fulfillment_service(engine, sender)
    assert await service.recover() == 1
    assert await service.recover() == 0
    assert await service.run_once() is False
    result = DeliveryResult(DeliveryState.SENT, message_id=99)
    assert await service.complete(claim, result) is False
    assert await service.complete(claim, result) is False
    sender.send.assert_not_awaited()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "UNCERTAIN"
        assert await connection.scalar(text("SELECT state FROM delivery_attempts")) == "UNCERTAIN"
        assert await connection.scalar(text("SELECT message_id FROM delivery_observations")) == 99
        assert await connection.scalar(text("SELECT count(*) FROM delivery_observations")) == 1
        assert await connection.scalar(text("SELECT status FROM orders")) == "FULFILLING"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
    assert "delivery_attention" in caplog.text and "fake-key" not in caplog.text


@pytest.mark.asyncio
async def test_rate_limit_retry_wait_respects_server_delay_without_immediate_resend(
    migrated_database,
):
    engine = migrated_database
    await paid_work(engine)
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(
        DeliveryState.RETRYABLE, code=DeliveryCode.RATE_LIMIT, retry_after=120
    )
    service = fulfillment_service(engine, sender)
    async with engine.connect() as connection:
        before = await connection.scalar(text("SELECT clock_timestamp()"))
    assert await service.run_once() is True
    assert await service.run_once() is False
    sender.send.assert_awaited_once()
    async with engine.connect() as connection:
        state, due_at = (
            await connection.execute(text("SELECT state, due_at FROM fulfillments"))
        ).one()
        assert state == "RETRY_WAIT" and due_at >= before + timedelta(seconds=120)
        assert await connection.scalar(text("SELECT state FROM delivery_attempts")) == "RETRYABLE"


@pytest.mark.asyncio
async def test_due_known_rejection_retries_same_unit_then_delivery_is_terminal(migrated_database):
    engine = migrated_database
    order, claim, claimed_at = await historical_claim(engine, sending=True)
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.SENT, message_id=33)
    service = fulfillment_service(engine, sender)
    historical = FulfillmentService(
        timed_uow(engine, claimed_at + timedelta(seconds=2)), service.cipher, sender, service.policy
    )
    assert (
        await historical.complete(
            claim,
            DeliveryResult(DeliveryState.RETRYABLE, code=DeliveryCode.RATE_LIMIT, retry_after=1),
        )
        is True
    )
    assert await service.run_once() is True
    assert await service.run_once() is False
    sender.send.assert_awaited_once()
    assert sender.send.await_args.args[0].context.unit_id == order.unit_id
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT send_count FROM fulfillments")) == 2
        assert await connection.scalar(text("SELECT count(*) FROM delivery_attempts")) == 2
        assert await connection.scalar(text("SELECT count(*) FROM inventory_units")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,expected",
    [
        (DeliveryResult(DeliveryState.FAILED, code=DeliveryCode.REJECTED), "FAILED"),
        (DeliveryResult(DeliveryState.UNCERTAIN, code=DeliveryCode.NETWORK), "UNCERTAIN"),
        (
            DeliveryResult(DeliveryState.RETRYABLE, code=DeliveryCode.RATE_LIMIT, retry_after=1),
            "FAILED",
        ),
    ],
)
async def test_permanent_uncertain_or_exhausted_result_never_requeues(
    migrated_database, result, expected
):
    engine = migrated_database
    await paid_work(engine)
    sender = AsyncMock()
    sender.send.return_value = result
    service = fulfillment_service(engine, sender, policy=DeliveryPolicy(max_attempts=1))
    await service.run_once()
    assert await service.recover() == 0 and await service.run_once() is False
    sender.send.assert_awaited_once()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == expected
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
        assert await connection.scalar(text("SELECT count(*) FROM payments")) == 1


@pytest.mark.asyncio
async def test_success_after_send_lease_before_recovery_is_only_evidence(migrated_database):
    engine = migrated_database
    _, claim, _ = await historical_claim(engine, sending=True)
    service = fulfillment_service(engine, AsyncMock())
    assert await service.complete(claim, DeliveryResult(DeliveryState.SENT, message_id=71)) is True
    assert await service.run_once() is False
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "UNCERTAIN"
        assert await connection.scalar(text("SELECT message_id FROM delivery_attempts")) is None
        assert await connection.scalar(text("SELECT message_id FROM delivery_observations")) == 71
