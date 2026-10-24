from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from telebotshop.infrastructure.database.readiness import payment_sandbox_ready
from tests.database import TEST_DATABASE_MARKER


@pytest.mark.asyncio
async def test_payment_sandbox_marker_uses_postgres_shared_catalog(migrated_database):
    # The fixture has already verified the exact disposable DB. Catalog changes never commit.
    async with migrated_database.begin() as connection:

        @asynccontextmanager
        async def connect():
            yield connection

        bound = SimpleNamespace(connect=connect)
        try:
            await connection.execute(
                text("COMMENT ON DATABASE telebotshop_test IS 'telebotshop:payment-v2-sandbox:v1'")
            )
            assert await payment_sandbox_ready(bound)
            await connection.execute(text("COMMENT ON DATABASE telebotshop_test IS 'wrong-marker'"))
            assert not await payment_sandbox_ready(bound)
        finally:
            await connection.rollback()
    async with migrated_database.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT pg_catalog.shobj_description(oid,'pg_database') "
                    "FROM pg_catalog.pg_database WHERE datname=current_database()"
                )
            )
            == TEST_DATABASE_MARKER
        )
