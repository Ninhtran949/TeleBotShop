import base64
import binascii
import json
import re
from decimal import Decimal
from pathlib import Path
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
    shop_terms_path: Path | None = Field(default=None, repr=False)
    telegram_environment: Literal["main", "test"] = "main"
    stars_checkout_enabled: bool = False
    credit_wallet_enabled: bool = False

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


class WebhookSettings(BotSettings):
    webhook_secret: SecretStr = Field(repr=False)

    @field_validator("webhook_secret")
    @classmethod
    def validate_webhook_secret(cls, value: SecretStr) -> SecretStr:
        if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value.get_secret_value()) is None:
            raise ValueError("WEBHOOK_SECRET must contain 32-256 allowed characters")
        return value


class PaymentV2Settings(Settings):
    shop_terms_path: Path | None = Field(default=None, repr=False)
    stars_checkout_enabled: bool = False
    credit_wallet_enabled: bool = True
    bank_topup_enabled: bool = True
    usdt_topup_enabled: bool = True
    external_payments_live_enabled: bool = False
    binance_readonly_enabled: bool = False
    binance_api_key: SecretStr | None = Field(default=None, repr=False)
    binance_api_secret: SecretStr | None = Field(default=None, repr=False)
    usdt_network: str | None = Field(default=None, min_length=1, max_length=200)
    usdt_token_contract: str | None = Field(default=None, min_length=1, max_length=200)
    usdt_minimum_confirmations: int = Field(default=3, ge=1, le=1_000_000)
    sandbox_usdt_vnd_rate: Decimal = Field(
        default=Decimal("25000"), gt=0, le=10**12, max_digits=30, decimal_places=18
    )
    sepay_bank_code: str = Field(default="FAKEBANK", min_length=1, max_length=32)
    sepay_account_number: str = Field(default="FAKE-ACCOUNT", min_length=1, max_length=100)
    sepay_webhook_secret: SecretStr | None = Field(default=None, repr=False)
    sepay_sandbox_api_token: SecretStr | None = Field(default=None, repr=False)
    pv2_operator_token: SecretStr | None = Field(default=None, repr=False)
    pv2_operator_id: int = Field(default=9001, gt=0, lt=2**52)

    @model_validator(mode="after")
    def sandbox_only(self):
        target = make_url(self.database_url.get_secret_value())
        if (
            self.app_env != "test"
            or self.external_payments_live_enabled
            or target.database != "telebotshop_payment_v2_sandbox"
            or target.host not in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("Payment V2 requires an isolated local sandbox database")
        for value in [self.sepay_webhook_secret, self.pv2_operator_token]:
            if value is not None and not 32 <= len(value.get_secret_value().encode()) <= 256:
                raise ValueError("Sandbox credentials require 32-256 bytes")
        if self.binance_readonly_enabled and any(
            value is None
            for value in [
                self.binance_api_key,
                self.binance_api_secret,
                self.usdt_network,
                self.usdt_token_contract,
            ]
        ):
            raise ValueError(
                "Read-only Binance reconciliation requires explicit credentials/network/token"
            )
        return self


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
