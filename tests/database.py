"""Guards for the explicitly provisioned, disposable PostgreSQL test database."""

import re

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

TEST_DATABASE_MARKER = "telebotshop:isolated-tests:v1"


def validate_test_database_url(app_env: str | None, dsn: str | None) -> URL:
    if app_env != "test" or not dsn:
        raise ValueError("Integration tests require APP_ENV=test and explicit TEST_DATABASE_URL")
    try:
        url = make_url(dsn)
    except (ArgumentError, ValueError):
        raise ValueError("Invalid TEST_DATABASE_URL") from None
    if (
        (url.drivername, url.host, url.port, url.database, url.username)
        != ("postgresql+asyncpg", "127.0.0.1", 55433, "telebotshop_test", "telebotshop_test")
        or not url.password
        or url.query
    ):
        raise ValueError("TEST_DATABASE_URL is outside the exact local test allowlist")
    return url


async def assert_test_database(connection: AsyncConnection) -> None:
    identity = (
        await connection.execute(
            text(
                "SELECT current_database(), current_user, "
                "pg_catalog.shobj_description(oid, 'pg_database'), "
                "current_setting('server_version_num')::integer / 10000 "
                "FROM pg_catalog.pg_database WHERE datname = current_database()"
            )
        )
    ).one()
    if tuple(identity) != ("telebotshop_test", "telebotshop_test", TEST_DATABASE_MARKER, 18):
        raise RuntimeError(
            "PostgreSQL test database identity/version/marker mismatch; refusing writes"
        )


async def drop_owned_schema(connection: AsyncConnection, schema: str, marker: str) -> None:
    if not re.fullmatch(r"tbs_test_[0-9a-f]{32}", schema):
        raise RuntimeError("Schema is outside the test-run allowlist")
    await assert_test_database(connection)
    ownership = (
        await connection.execute(
            text(
                "SELECT pg_catalog.pg_get_userbyid(nspowner), "
                "pg_catalog.obj_description(oid, 'pg_namespace') "
                "FROM pg_catalog.pg_namespace WHERE nspname = :schema"
            ),
            {"schema": schema},
        )
    ).one_or_none()
    if ownership is None or tuple(ownership) != ("telebotshop_test", marker):
        raise RuntimeError("Test schema ownership marker mismatch; refusing cleanup")
    quoted = connection.engine.dialect.identifier_preparer.quote(schema)
    await connection.execute(text(f"DROP SCHEMA {quoted} CASCADE"))


async def cleanup_schema(engine: AsyncEngine, schema: str, marker: str) -> None:
    async with engine.begin() as connection:
        await drop_owned_schema(connection, schema, marker)
