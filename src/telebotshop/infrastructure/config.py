from typing import Literal

from pydantic import Field, IPvAnyAddress, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


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
