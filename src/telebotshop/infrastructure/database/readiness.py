import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.infrastructure.database.schema import SCHEMA_HEAD


async def database_ready(engine: AsyncEngine, *, timeout: float = 2) -> bool:
    try:
        async with asyncio.timeout(timeout):
            async with engine.connect() as connection:
                revisions = (
                    await connection.scalars(text("SELECT version_num FROM alembic_version"))
                ).all()
                return revisions == [SCHEMA_HEAD]
    except (SQLAlchemyError, OSError, TimeoutError):
        return False


async def payment_sandbox_ready(engine: AsyncEngine) -> bool:
    if not await database_ready(engine):
        return False
    try:
        async with asyncio.timeout(2):
            async with engine.connect() as connection:
                return (
                    await connection.scalar(
                        text(
                            "SELECT pg_catalog.shobj_description(oid,'pg_database') "
                            "FROM pg_catalog.pg_database "
                            "WHERE datname=current_database()"
                        )
                    )
                    == "telebotshop:payment-v2-sandbox:v1"
                )
    except (SQLAlchemyError, OSError, TimeoutError):
        return False
