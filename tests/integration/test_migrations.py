from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.infrastructure.database.schema import SCHEMA_HEAD


def upgrade(connection: Connection, revision: str = "head") -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


@pytest.mark.asyncio
async def test_clean_schema_upgrades_to_baseline(database: AsyncEngine) -> None:
    async with database.begin() as connection:
        assert await connection.scalar(text("SELECT to_regclass('alembic_version')")) is None
        await connection.run_sync(upgrade)
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_HEAD
        )


@pytest.mark.asyncio
async def test_upgrade_from_base_is_repeatable_and_preserves_existing_rows(
    database: AsyncEngine,
) -> None:
    async with database.begin() as connection:
        await connection.run_sync(upgrade, "base")
        await connection.execute(text("CREATE TABLE migration_probe (value integer NOT NULL)"))
        await connection.execute(text("INSERT INTO migration_probe VALUES (42)"))
        await connection.run_sync(upgrade)
        await connection.run_sync(upgrade)
        assert await connection.scalar(text("SELECT value FROM migration_probe")) == 42
        assert (
            await connection.scalars(text("SELECT version_num FROM alembic_version"))
        ).all() == [SCHEMA_HEAD]
