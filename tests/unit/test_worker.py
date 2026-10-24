import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from telebotshop.infrastructure.config import WorkerSettings
from telebotshop.workers.fulfillment import run_forever
from tests.telegram import FAKE_TOKEN


@pytest.mark.asyncio
async def test_worker_recovers_before_claim_and_idle_wait_is_cancellable():
    service = AsyncMock()
    service.run_once.return_value = False
    notifications = AsyncMock()
    notifications.run_once.return_value = False
    task = asyncio.create_task(run_forever(service, notifications, poll_seconds=30))
    try:
        async with asyncio.timeout(1):
            while not service.run_once.await_count:
                await asyncio.sleep(0)
        assert service.recover.await_count == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_notification_failure_is_redacted_and_does_not_stop_paid_work(caplog):
    service, notifications = AsyncMock(), AsyncMock()
    service.run_once.side_effect = [True, False]
    notifications.discover.side_effect = OSError("FAKE_SECRET_NOTIFICATIONS")
    notifications.run_once.side_effect = OSError("FAKE_SECRET_NOTIFICATIONS")
    task = asyncio.create_task(run_forever(service, notifications, poll_seconds=30))
    try:
        async with asyncio.timeout(1):
            while service.run_once.await_count < 2:
                await asyncio.sleep(0)
        assert "notification_worker_failed" in caplog.text
        assert "FAKE_SECRET_NOTIFICATIONS" not in caplog.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("worker_claim_seconds", 0),
        ("worker_timeout_seconds", 21),
        ("worker_send_seconds", 11),
        ("worker_max_attempts", 6),
        ("worker_poll_seconds", 0),
    ],
)
def test_worker_settings_validate_budgets_without_exposing_token(field, value):
    with pytest.raises(ValidationError) as error:
        WorkerSettings(
            app_env="test",
            database_url="postgresql+asyncpg://fake:fake@127.0.0.1/fake",
            bot_token=FAKE_TOKEN,
            owner_id=9001,
            **{field: value},
        )
    assert FAKE_TOKEN not in str(error.value)
