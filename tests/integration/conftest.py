import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.database import assert_test_database, cleanup_schema, validate_test_database_url


@pytest_asyncio.fixture
async def database() -> AsyncIterator[AsyncEngine]:
    try:
        url = validate_test_database_url(os.getenv("APP_ENV"), os.getenv("TEST_DATABASE_URL"))
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)
    schema = f"tbs_test_{uuid4().hex}"
    marker = f"telebotshop-test-run:{uuid4().hex}"
    async with AsyncExitStack() as stack:
        admin = create_async_engine(url, hide_parameters=True, connect_args={"timeout": 2})
        stack.push_async_callback(admin.dispose)
        async with admin.begin() as connection:
            await assert_test_database(connection)
            quoted = admin.dialect.identifier_preparer.quote(schema)
            await connection.execute(text(f"CREATE SCHEMA {quoted}"))
            # Both identifiers and marker are generated UUIDs, never values from a DSN or payload.
            await connection.execute(text(f"COMMENT ON SCHEMA {quoted} IS '{marker}'"))
        stack.push_async_callback(cleanup_schema, admin, schema, marker)
        engine = create_async_engine(
            url,
            hide_parameters=True,
            connect_args={"timeout": 2, "server_settings": {"search_path": schema}},
        )
        stack.push_async_callback(engine.dispose)
        yield engine


@pytest_asyncio.fixture
async def migrated_database(database: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    async with database.begin() as connection:
        config = Config("alembic.ini")

        def upgrade(sync_connection: Connection) -> None:
            config.attributes["connection"] = sync_connection
            command.upgrade(config, "head")

        await connection.run_sync(upgrade)
    yield database
