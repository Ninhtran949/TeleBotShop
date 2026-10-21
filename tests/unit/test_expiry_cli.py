import sys
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from telebotshop import bootstrap
from telebotshop.infrastructure.config import Settings

FAKE_DSN = "postgresql+asyncpg://fake:fake-secret@127.0.0.1:55433/fake"


def test_expiry_cli_uses_no_bot_or_crypto_configuration(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["telebotshop", "expire-orders"])
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", FAKE_DSN)
    run = AsyncMock(return_value=(3, 2))
    monkeypatch.setattr(bootstrap, "run_expiry", run)
    monkeypatch.setattr(bootstrap, "Bot", lambda *args: pytest.fail("Bot must not start"))
    monkeypatch.setattr(bootstrap, "InventorySettings", lambda: pytest.fail("No crypto config"))
    assert bootstrap.main() == 0
    assert "Expired orders: 3; uncertain invoices: 2" in capsys.readouterr().out
    assert type(run.await_args.args[0]) is Settings


@pytest.mark.asyncio
async def test_expiry_startup_refuses_outdated_schema_and_disposes_engine(monkeypatch):
    engine = create_async_engine(FAKE_DSN)
    dispose = AsyncMock()
    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(bootstrap, "database_ready", AsyncMock(return_value=False))
    with pytest.raises(bootstrap.ExpiryStartupError):
        await bootstrap.run_expiry(Settings(app_env="test", database_url=FAKE_DSN))
    dispose.assert_awaited_once()


def test_expiry_failure_is_sanitized(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["telebotshop", "expire-orders"])
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", FAKE_DSN)
    monkeypatch.setattr(bootstrap, "run_expiry", AsyncMock(side_effect=OSError(FAKE_DSN)))
    assert bootstrap.main() == 1
    output = capsys.readouterr().err
    assert "Order maintenance unavailable" in output
    assert "fake-secret" not in output
