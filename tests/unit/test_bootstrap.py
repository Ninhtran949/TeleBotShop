import os
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from telebotshop import bootstrap
from telebotshop.infrastructure.config import Settings

FAKE_DSN = "postgresql+asyncpg://fake:fake-secret@127.0.0.1:55433/fake"


@pytest.mark.parametrize(
    ("app_env", "dsn"), [("invalid", FAKE_DSN), ("test", FAKE_DSN + "?unsupported_option=1")]
)
def test_startup_rejects_invalid_environment(
    monkeypatch: pytest.MonkeyPatch, app_env: str, dsn: str
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("DATABASE_URL", dsn)
    with pytest.raises(ValidationError):
        bootstrap.create_app()


def test_cli_configuration_error_is_sanitized() -> None:
    environment = {**os.environ, "APP_ENV": "invalid", "DATABASE_URL": FAKE_DSN}
    result = subprocess.run(
        [sys.executable, "-I", "-m", "telebotshop"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Invalid application configuration" in result.stderr
    assert "fake-secret" not in result.stdout + result.stderr
    assert FAKE_DSN not in result.stdout + result.stderr


def test_shutdown_disposes_pool_even_when_handler_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(FAKE_DSN)
    dispose = AsyncMock()
    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(
        bootstrap, "database_ready", AsyncMock(side_effect=RuntimeError("probe error"))
    )
    app = bootstrap.create_app(Settings(app_env="test", database_url=FAKE_DSN))
    with pytest.raises(RuntimeError, match="probe error"):
        with TestClient(app) as client:
            client.get("/health/ready")
    dispose.assert_awaited_once()
