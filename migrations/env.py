import asyncio

from alembic import context
from alembic.util import CommandError
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from telebotshop.infrastructure.config import Settings
from telebotshop.infrastructure.database.models.refunds import metadata


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, compare_server_default=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    settings = Settings()
    engine = create_async_engine(
        settings.database_url.get_secret_value(), poolclass=NullPool, hide_parameters=True
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise CommandError("This baseline supports online migrations only")

connection = context.config.attributes.get("connection")
if connection is None:
    asyncio.run(run_online())
elif isinstance(connection, Connection):
    run_migrations(connection)
else:
    raise TypeError("Expected an explicit SQLAlchemy migration connection")
