import json
from functools import partial
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telebotshop.application.services.notifications import NotificationService
from telebotshop.domain.models.support import TicketKind
from telebotshop.infrastructure.database.models.notifications import metadata
from telebotshop.infrastructure.database.schema import SCHEMA_HEAD
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import paid_work
from tests.integration.test_notification_failures import Sender
from tests.orders import setup_orders
from tests.support import support_service


async def migrate(engine, revision, *, down=False):
    async with engine.begin() as c:

        def run(connection):
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            (command.downgrade if down else command.upgrade)(config, revision)

        await c.run_sync(run)


async def snapshot(engine, tables):
    result = {}
    async with engine.connect() as c:
        for name in tables:
            quoted = c.dialect.identifier_preparer.quote(name)
            records = (
                await c.scalars(text(f"SELECT to_jsonb(t)::text FROM {quoted} t ORDER BY 1"))
            ).all()
            # Additive PV2 defaults are part of the oracle; every predecessor field stays checked.
            defaults = {
                "orders": {"payment_method": "TELEGRAM_STARS"},
                "order_items": {"price_credits": None},
                "payments": {"payment_method": "TELEGRAM_STARS", "wallet_journal_id": None},
            }.get(name, {})
            normalized = []
            for record in records:
                value = json.loads(record)
                for key, default in defaults.items():
                    value.setdefault(key, default)
                normalized.append(json.dumps(value, sort_keys=True))
            result[name] = sorted(normalized)
    return result


@pytest.mark.asyncio
async def test_notification_upgrade_downgrade_preserves_all_predecessor_data_and_functions(
    database,
):
    engine = database
    await migrate(engine, "0013_refunds")
    await paid_work(engine)
    await support_service(engine).create(101, TicketKind.PAYMENT, "FAKE encrypted support", uuid4())
    async with engine.connect() as c:
        tables = (
            await c.scalars(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() "
                    "AND tablename<>'alembic_version' ORDER BY tablename"
                )
            )
        ).all()
        functions = dict(
            (
                await c.execute(
                    text(
                        "SELECT proname,pg_get_functiondef(oid) FROM pg_proc "
                        "WHERE pronamespace=current_schema()::regnamespace"
                    )
                )
            ).all()
        )
    before = await snapshot(engine, tables)
    for revision, down in [("head", False), ("0013_refunds", True), ("head", False)]:
        await migrate(engine, revision, down=down)
        assert await snapshot(engine, tables) == before
        async with engine.connect() as c:
            after = dict(
                (
                    await c.execute(
                        text(
                            "SELECT proname,pg_get_functiondef(oid) FROM pg_proc "
                            "WHERE pronamespace=current_schema()::regnamespace"
                        )
                    )
                ).all()
            )
            assert all(after[name] == value for name, value in functions.items())
    async with engine.connect() as c:
        diffs = await c.run_sync(
            lambda connection: compare_metadata(
                MigrationContext.configure(connection, opts={"compare_server_default": True}),
                metadata,
            )
        )
    assert diffs == []


@pytest.mark.asyncio
async def test_notification_history_blocks_downgrade_without_deleting_rows(migrated_database):
    engine = migrated_database
    await setup_orders(engine)
    service = NotificationService(partial(unit_of_work, engine), Sender(engine), threshold=5)
    await service.discover()
    with pytest.raises(RuntimeError, match="Cannot downgrade notification history"):
        await migrate(engine, "0013_refunds", down=True)
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM notifications")) == 1
        assert await c.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_HEAD


@pytest.mark.asyncio
async def test_native_notification_guards_reject_identity_reset_and_unproven_outcomes(
    migrated_database,
):
    engine = migrated_database
    await setup_orders(engine)
    service = NotificationService(partial(unit_of_work, engine), Sender(engine), threshold=5)
    await service.discover()
    async with unit_of_work(engine) as work:
        note = await work.notifications.next()
        await work.notifications.start(note, await work.orders.now())
        await work.commit()
    for sql in [
        "UPDATE notifications SET recipient_id=(SELECT id FROM users WHERE telegram_id=102)",
        "UPDATE notifications SET revision='changed'",
        "UPDATE notifications SET deadline=NULL",
        "UPDATE notifications SET state='READY',started_at=NULL,deadline=NULL",
        "UPDATE notifications SET state='SENT',finished_at=clock_timestamp()",
        "UPDATE notifications SET state='SENT',finished_at=clock_timestamp(),"
        "observed_state='SENT',observed_at=clock_timestamp()",
        "DELETE FROM notifications",
        "TRUNCATE notifications",
    ]:
        with pytest.raises(DBAPIError):
            async with engine.begin() as c:
                await c.execute(text(sql))
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM notifications")) == "SENDING"
