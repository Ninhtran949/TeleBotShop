from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from telebotshop.domain.models.fulfillment import DeliveryResult, DeliveryState
from tests.fulfillment import fulfillment_service, paid_work


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE fulfillments SET state = 'READY', active_attempt_id = NULL",
        "UPDATE fulfillments SET send_count = 0",
        "UPDATE fulfillments SET destination_telegram_id = 102",
        "UPDATE delivery_attempts SET lease_expires_at = lease_expires_at + interval '1 minute'",
        "UPDATE delivery_attempts SET state = 'CLAIMED', completed_at = NULL, message_id = NULL",
        "DELETE FROM delivery_attempts",
        "TRUNCATE delivery_attempts CASCADE",
        "UPDATE delivery_observations SET message_id = 123",
        "DELETE FROM delivery_observations",
    ],
)
async def test_terminal_attempt_work_and_observations_cannot_be_rewritten(migrated_database, sql):
    engine = migrated_database
    await paid_work(engine)
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.SENT, message_id=15)
    await fulfillment_service(engine, sender).run_once()
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(text(sql))


@pytest.mark.asyncio
async def test_observation_rejects_freeform_error_payload(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    claim = await fulfillment_service(engine, AsyncMock()).claim()
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO delivery_observations(attempt_id, digest, state, error_code) "
                    "VALUES (:attempt, decode(repeat('11', 32), 'hex'), "
                    "'FAILED', 'FAKE-private-error')"
                ),
                {"attempt": claim.attempt_id},
            )


@pytest.mark.asyncio
async def test_completion_cannot_rewrite_the_durable_sending_timestamp(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    service = fulfillment_service(engine, AsyncMock())
    claim = await service.claim()
    await service.prepare(claim)
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE delivery_attempts SET state = 'SENT', sending_at = claimed_at, "
                    "completed_at = clock_timestamp(), message_id = 1"
                )
            )
            await connection.execute(text("UPDATE fulfillments SET state = 'SENT'"))
            await connection.execute(text("UPDATE orders SET status = 'DELIVERED'"))
