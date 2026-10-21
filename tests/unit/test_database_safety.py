import pytest

from tests.database import validate_test_database_url

FAKE_DSN = "postgresql+asyncpg://telebotshop_test:test-local-only@127.0.0.1:55433/telebotshop_test"


def test_explicit_test_target_is_allowed() -> None:
    url = validate_test_database_url("test", FAKE_DSN)
    assert url.database == "telebotshop_test"


@pytest.mark.parametrize("app_env", [None, "development", "production"])
def test_wrong_environment_is_refused(app_env: str | None) -> None:
    with pytest.raises(ValueError):
        validate_test_database_url(app_env, FAKE_DSN)


@pytest.mark.parametrize(
    "dsn",
    [
        None,
        "not a database URL",
        FAKE_DSN.replace("asyncpg", "psycopg"),
        FAKE_DSN.replace("127.0.0.1", "remote.example"),
        FAKE_DSN.replace("55433", "5432"),
        FAKE_DSN.replace("/telebotshop_test", "/production"),
        FAKE_DSN.replace("telebotshop_test:", "postgres:"),
        FAKE_DSN + "?host=remote.example",
    ],
)
def test_non_allowlisted_target_is_refused_without_echoing_dsn(dsn: str | None) -> None:
    with pytest.raises(ValueError) as error:
        validate_test_database_url("test", dsn)
    assert "test-local-only" not in str(error.value)
