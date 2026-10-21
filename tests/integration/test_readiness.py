import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from telebotshop.infrastructure.database.readiness import database_ready


def migrate(connection: Connection) -> None:
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


@pytest.mark.asyncio
async def test_readiness_requires_exact_schema_revision(database: AsyncEngine) -> None:
    assert not await database_ready(database)
    async with database.begin() as connection:
        await connection.run_sync(migrate)
    assert await database_ready(database)
    async with database.begin() as connection:
        await connection.execute(text("UPDATE alembic_version SET version_num = 'wrong-revision'"))
    assert not await database_ready(database)
    assert database.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_readiness_timeout_releases_connection_under_schema_lock(
    database: AsyncEngine,
) -> None:
    async with database.begin() as connection:
        await connection.run_sync(migrate)
    async with database.begin() as locker:
        await locker.execute(text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"))
        assert not await asyncio.wait_for(database_ready(database, timeout=0.05), timeout=1)
    assert await database_ready(database)
    assert database.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_readiness_is_false_when_database_is_unreachable() -> None:
    engine = create_async_engine("postgresql+asyncpg://fake:fake@127.0.0.1:1/unreachable")
    try:
        assert not await asyncio.wait_for(database_ready(engine, timeout=0.1), timeout=1)
    finally:
        await engine.dispose()
