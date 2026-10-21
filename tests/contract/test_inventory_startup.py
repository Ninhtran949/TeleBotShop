from unittest.mock import Mock

import pytest

from telebotshop import bootstrap
from tests.telegram import FAKE_TOKEN


@pytest.mark.parametrize("keys", [None, "FAKE-private-configuration"])
def test_polling_refuses_missing_or_bad_crypto_config_before_runtime(monkeypatch, capsys, keys):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://fake:fake@127.0.0.1/fake")
    monkeypatch.setenv("BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("OWNER_ID", "9001")
    for name in (
        "INVENTORY_ENCRYPTION_KEYS",
        "INVENTORY_CURRENT_KEY_VERSION",
        "INVENTORY_DEDUP_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    if keys is not None:
        monkeypatch.setenv("INVENTORY_ENCRYPTION_KEYS", keys)
    monkeypatch.setattr("sys.argv", ["telebotshop", "polling"])
    engine = Mock(side_effect=AssertionError("Runtime must not start"))
    monkeypatch.setattr(bootstrap, "create_async_engine", engine)
    assert bootstrap.main() == 2
    assert not engine.called
    captured = capsys.readouterr()
    assert "Invalid application configuration" in captured.err
    assert "FAKE-private" not in captured.err
