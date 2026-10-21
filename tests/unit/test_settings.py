import pytest
from pydantic import ValidationError

from telebotshop.infrastructure.config import Settings

FAKE_DSN = "postgresql+asyncpg://test_user:fake-secret@127.0.0.1:55433/test_db"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "DATABASE_URL", "HTTP_HOST", "HTTP_PORT"):
        monkeypatch.delenv(name, raising=False)


def test_missing_configuration_fails() -> None:
    with pytest.raises(ValidationError):
        Settings()


def test_configuration_comes_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", FAKE_DSN)
    settings = Settings()
    assert settings.app_env == "development"
    assert settings.database_url.get_secret_value() == FAKE_DSN
    assert str(settings.http_host) == "127.0.0.1"
    assert settings.http_port == 8000
    assert "fake-secret" not in repr(settings)
    assert FAKE_DSN not in str(settings)


@pytest.mark.parametrize(
    "dsn",
    [
        "sqlite:///test.db",
        "postgresql://test_user:fake-secret@localhost/test_db",
        "postgresql+asyncpg://test_user:fake-secret@/test_db",
        "postgresql+asyncpg://localhost/test_db",
        "postgresql+asyncpg://test_user:fake-secret@localhost",
        "postgresql+asyncpg://test_user:fake-secret@localhost:invalid/test_db",
        FAKE_DSN.replace("55433", "0"),
        FAKE_DSN.replace("55433", "65536"),
        FAKE_DSN + "?unsupported_option=1",
        FAKE_DSN + "?ssl=invalid",
        FAKE_DSN + "?ssl=require&ssl=disable",
    ],
)
def test_bad_database_configuration_is_rejected_without_echoing_secrets(dsn: str) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(app_env="test", database_url=dsn)
    assert "fake-secret" not in str(error.value)
    assert dsn not in str(error.value)


def test_supported_ssl_mode_is_preserved() -> None:
    dsn = FAKE_DSN + "?ssl=verify-full"
    assert Settings(app_env="test", database_url=dsn).database_url.get_secret_value() == dsn


@pytest.mark.parametrize(
    "overrides",
    [{"app_env": "typo"}, {"http_port": 0}, {"http_port": 65536}, {"http_host": "bad host"}],
)
def test_bad_runtime_configuration_fails(overrides: dict[str, str | int]) -> None:
    values = {"app_env": "test", "database_url": FAKE_DSN, **overrides}
    with pytest.raises(ValidationError):
        Settings(**values)
