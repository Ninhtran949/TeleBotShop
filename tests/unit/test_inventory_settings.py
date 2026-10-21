import base64
import json

import pytest
from pydantic import ValidationError

from telebotshop.infrastructure.config import InventorySettings


def key(value: bytes) -> str:
    return base64.b64encode(value * 32).decode()


def test_versioned_keys_load_from_environment_and_are_redacted(monkeypatch) -> None:
    monkeypatch.delenv("LOW_STOCK_THRESHOLD", raising=False)
    monkeypatch.setenv("INVENTORY_ENCRYPTION_KEYS", json.dumps({"1": key(b"a"), "2": key(b"b")}))
    monkeypatch.setenv("INVENTORY_CURRENT_KEY_VERSION", "2")
    monkeypatch.setenv("INVENTORY_DEDUP_KEY", key(b"d"))
    settings = InventorySettings()
    assert settings.keyring() == {1: b"a" * 32, 2: b"b" * 32}
    assert settings.inventory_current_key_version == 2 and settings.low_stock_threshold == 5
    assert key(b"a") not in repr(settings) and key(b"d") not in settings.model_dump_json()


@pytest.mark.parametrize(
    "updates",
    [
        {"inventory_encryption_keys": "FAKE-secret"},
        {"inventory_encryption_keys": "{}"},
        {"inventory_encryption_keys": json.dumps([["1", key(b"a")]])},
        {"inventory_encryption_keys": '{"1":"' + key(b"a") + '","1":"' + key(b"b") + '"}'},
        {"inventory_encryption_keys": json.dumps({"0": key(b"a")})},
        {"inventory_encryption_keys": json.dumps({"1": "FAKE-secret"})},
        {"inventory_current_key_version": 2},
        {"inventory_dedup_key": key(b"a")},
        {"inventory_dedup_key": "FAKE-secret"},
        {"low_stock_threshold": -1},
    ],
)
def test_invalid_missing_or_reused_keys_fail_without_echoing_input(updates) -> None:
    data = {
        "inventory_encryption_keys": json.dumps({"1": key(b"a")}),
        "inventory_current_key_version": 1,
        "inventory_dedup_key": key(b"d"),
    }
    data.update(updates)
    with pytest.raises(ValidationError) as captured:
        InventorySettings(**data)
    assert "FAKE-secret" not in str(captured.value) and key(b"a") not in str(captured.value)
