import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from telebotshop.infrastructure.config import BotSettings, Settings
from tests.telegram import FAKE_TOKEN

FAKE_DSN = "postgresql+asyncpg://fake:fake-secret@127.0.0.1:55433/fake"


@pytest.mark.parametrize("owner_id", [True, 0, -1, 2**52, "1.5", "９００１", "9001;command"])
def test_bot_owner_id_is_explicit_validated_and_not_coerced_from_bool(owner_id: object) -> None:
    with pytest.raises(ValidationError):
        BotSettings(app_env="test", database_url=FAKE_DSN, bot_token=FAKE_TOKEN, owner_id=owner_id)


@pytest.mark.parametrize(
    "token", ["", "missing-colon", "1:secret whitespace", "0:secret", "1:../secret"]
)
def test_bot_token_validation_never_echoes_token(token: str) -> None:
    with pytest.raises(ValidationError) as failure:
        BotSettings(app_env="test", database_url=FAKE_DSN, bot_token=token, owner_id=9001)
    assert "secret whitespace" not in str(failure.value)
    assert "../secret" not in str(failure.value)


def test_health_settings_stay_independent_and_polling_is_development_only() -> None:
    Settings(app_env="production", database_url=FAKE_DSN)
    bot = BotSettings(app_env="test", database_url=FAKE_DSN, bot_token=FAKE_TOKEN, owner_id="9001")
    assert bot.owner_id == 9001
    assert FAKE_TOKEN not in str(bot) + repr(bot)
    with pytest.raises(ValidationError):
        BotSettings(
            app_env="production", database_url=FAKE_DSN, bot_token=FAKE_TOKEN, owner_id=9001
        )


def test_polling_cli_missing_owner_or_token_fails_without_network_or_secret_output() -> None:
    environment = {
        key: value for key, value in os.environ.items() if key not in {"BOT_TOKEN", "OWNER_ID"}
    }
    environment.update(APP_ENV="test", DATABASE_URL=FAKE_DSN)
    result = subprocess.run(
        [sys.executable, "-I", "-m", "telebotshop", "polling"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Invalid application configuration" in result.stderr
    assert "fake-secret" not in result.stdout + result.stderr
