import base64
import binascii
import json
import re
from typing import Literal

from pydantic import Field, IPvAnyAddress, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.fulfillment import DeliveryPolicy
from telebotshop.domain.models.identity import validate_telegram_id


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"]
    database_url: SecretStr = Field(repr=False)
    http_host: IPvAnyAddress = "127.0.0.1"
    http_port: int = Field(default=8000, ge=1, le=65535)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except (ArgumentError, ValueError):
            raise ValueError("DATABASE_URL must be a PostgreSQL asyncpg URL") from None
        if url.drivername != "postgresql+asyncpg" or not all(
            (url.host, url.database, url.username, url.password)
        ):
            raise ValueError("DATABASE_URL requires asyncpg, host, database and credentials")
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError("DATABASE_URL port must be between 1 and 65535")
        # shortcut: only the ssl DSN option is supported; extend for a concrete integration need.
        if set(url.query) - {"ssl"} or url.query.get("ssl") not in {
            None,
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ValueError("DATABASE_URL query must contain only a supported ssl mode")
        return value


class BotSettings(Settings):
    app_env: Literal["development", "test"]
    bot_token: SecretStr = Field(repr=False)
    owner_id: int = Field(repr=False)

    @field_validator("owner_id", mode="before")
    @classmethod
    def validate_owner_id(cls, value: object) -> int:
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,16}", value):
            value = int(value)
        if type(value) is not int:
            raise ValueError("OWNER_ID must be a Telegram user ID")
        validate_telegram_id(value)
        return value

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if not re.fullmatch(r"[1-9][0-9]{0,15}:[A-Za-z0-9_-]{20,128}", token):
            raise ValueError("BOT_TOKEN is invalid")
        return value


def decode_inventory_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) == 32 and base64.b64encode(decoded).decode() == value:
            return decoded
    except (ValueError, binascii.Error):
        pass
    raise ValueError("Inventory keys must be canonical base64 of 32 bytes")


def decode_inventory_keyring(value: SecretStr) -> dict[int, bytes]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError
        return result

    try:
        record = json.loads(value.get_secret_value(), object_pairs_hook=unique_object)
        if not isinstance(record, dict) or not 1 <= len(record) <= 32:
            raise ValueError
        keys = {}
        for version, key in record.items():
            if not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", version):
                raise ValueError
            number = int(version)
            if number >= 2**31 or number in keys or not isinstance(key, str):
                raise ValueError
            keys[number] = decode_inventory_key(key)
        return keys
    except (ValueError, TypeError):
        raise ValueError("Invalid inventory encryption keyring") from None


class InventorySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, hide_input_in_errors=True)

    inventory_encryption_keys: SecretStr = Field(repr=False)
    inventory_current_key_version: int = Field(ge=1, lt=2**31)
    inventory_dedup_key: SecretStr = Field(repr=False)
    low_stock_threshold: int = Field(default=5, ge=0, le=1_000_000)

    @field_validator("inventory_encryption_keys")
    @classmethod
    def validate_keyring(cls, value: SecretStr) -> SecretStr:
        decode_inventory_keyring(value)
        return value

    @field_validator("inventory_dedup_key")
    @classmethod
    def validate_dedup_key(cls, value: SecretStr) -> SecretStr:
        decode_inventory_key(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def validate_distinct_current_key(self) -> "InventorySettings":
        keys = self.keyring()
        if self.inventory_current_key_version not in keys or self.dedup_key() in keys.values():
            raise ValueError("Current encryption key missing or dedup key reused")
        return self

    def keyring(self) -> dict[int, bytes]:
        return decode_inventory_keyring(self.inventory_encryption_keys)

    def dedup_key(self) -> bytes:
        return decode_inventory_key(self.inventory_dedup_key.get_secret_value())


class WorkerSettings(BotSettings):
    worker_claim_seconds: int = Field(default=30, ge=1, le=300)
    worker_send_seconds: int = Field(default=60, ge=1, le=300)
    worker_timeout_seconds: int = Field(default=10, ge=1, le=20)
    worker_max_attempts: int = Field(default=3, ge=1, le=5)
    worker_poll_seconds: float = Field(default=1, ge=0.05, le=60)

    @model_validator(mode="after")
    def validate_delivery_policy(self) -> "WorkerSettings":
        try:
            self.policy()
        except InvalidInput:
            raise ValueError("Invalid worker delivery policy") from None
        return self

    def policy(self) -> DeliveryPolicy:
        return DeliveryPolicy(
            claim_seconds=self.worker_claim_seconds,
            send_seconds=self.worker_send_seconds,
            timeout_seconds=self.worker_timeout_seconds,
            max_attempts=self.worker_max_attempts,
        )
