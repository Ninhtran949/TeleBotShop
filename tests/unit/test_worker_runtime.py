import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from telebotshop import bootstrap
from telebotshop.infrastructure.config import WorkerSettings
from tests.inventory import fake_inventory_settings
from tests.telegram import FAKE_TOKEN, TelegramRecorder


def settings():
    return WorkerSettings(
        app_env="test",
        database_url="postgresql+asyncpg://fake:fake@127.0.0.1/fake",
        bot_token=FAKE_TOKEN,
        owner_id=9001,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ok", "cancelled", "error"])
async def test_worker_startup_recovery_wiring_and_resources_close(monkeypatch, outcome):
    telegram = TelegramRecorder(monkeypatch)
    close = AsyncMock(wraps=telegram.bot.session.close)
    monkeypatch.setattr(telegram.bot.session, "close", close)
    engine = Mock(dispose=AsyncMock())
    identity = Mock(bootstrap_owner=AsyncMock())
    inventory = Mock(check_keys=AsyncMock())
    service = Mock()
    loop = AsyncMock()
    if outcome == "cancelled":
        loop.side_effect = asyncio.CancelledError()
    elif outcome == "error":
        loop.side_effect = SQLAlchemyError("FAKE-private-DSN")
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(bootstrap, "database_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(bootstrap, "IdentityService", lambda *args: identity)
    monkeypatch.setattr(bootstrap, "InventoryService", lambda *args: inventory)
    monkeypatch.setattr(bootstrap, "FulfillmentService", lambda *args: service)
    monkeypatch.setattr(bootstrap, "Bot", lambda *args, **kwargs: telegram.bot)
    monkeypatch.setattr(bootstrap, "run_forever", loop)
    try:
        if outcome == "ok":
            await bootstrap.run_worker(settings(), fake_inventory_settings())
        else:
            with pytest.raises(
                asyncio.CancelledError if outcome == "cancelled" else SQLAlchemyError
            ):
                await bootstrap.run_worker(settings(), fake_inventory_settings())
        identity.bootstrap_owner.assert_awaited_once_with(9001)
        inventory.check_keys.assert_awaited_once()
        assert loop.await_count == 1
        assert loop.await_args.args[0] is service
        assert isinstance(loop.await_args.args[1], bootstrap.NotificationService)
        assert loop.await_args.kwargs == {"poll_seconds": 1}
        close.assert_awaited_once()
        engine.dispose.assert_awaited_once()
        assert not telegram.calls
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_worker_outdated_schema_fails_before_bot_and_closes_engine(monkeypatch):
    engine = Mock(dispose=AsyncMock())
    bot = Mock(side_effect=AssertionError("No bot before readiness"))
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(bootstrap, "database_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(bootstrap, "Bot", bot)
    with pytest.raises(bootstrap.PollingStartupError):
        await bootstrap.run_worker(settings(), fake_inventory_settings())
    bot.assert_not_called()
    engine.dispose.assert_awaited_once()
