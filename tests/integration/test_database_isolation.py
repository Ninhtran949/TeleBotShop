import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.database import assert_test_database, drop_owned_schema


@pytest.mark.asyncio
async def test_cleanup_refuses_schema_without_matching_run_marker(database: AsyncEngine) -> None:
    async with database.begin() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
        with pytest.raises(RuntimeError, match="ownership"):
            await drop_owned_schema(connection, schema, "another-test-run")
        assert await connection.scalar(text("SELECT to_regnamespace(:schema)"), {"schema": schema})
        with pytest.raises(RuntimeError):
            await drop_owned_schema(connection, "public", "another-test-run")


@pytest.mark.asyncio
async def test_database_without_external_test_marker_is_refused(database: AsyncEngine) -> None:
    async with database.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("COMMENT ON DATABASE telebotshop_test IS 'not-owned'"))
            with pytest.raises(RuntimeError, match="marker"):
                await assert_test_database(connection)
        finally:
            await transaction.rollback()
