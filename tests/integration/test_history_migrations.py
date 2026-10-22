import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_migrations import upgrade
from tests.integration.test_resource_migrations import legacy_delivery
from tests.resends import RecordingSender, delivered_order, resend_service


def downgrade(connection):
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0010_resources")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["READY", "CLAIMED", "SENDING", "SENT", "RETRY_WAIT", "UNCERTAIN", "FAILED"]
)
async def test_t11_upgrade_and_empty_downgrade_preserve_t10_work(database, state):
    engine = database
    async with engine.begin() as connection:
        await connection.run_sync(upgrade, "0010_resources")
    await legacy_delivery(engine, state)
    tables = (
        "orders",
        "order_items",
        "payments",
        "fulfillments",
        "delivery_attempts",
        "inventory_units",
        "audit_logs",
    )
    async with engine.begin() as connection:
        before = {
            table: (await connection.execute(text(f"SELECT * FROM {table}"))).mappings().all()
            for table in tables
        }
        await connection.run_sync(upgrade)
        await connection.run_sync(downgrade)
        await connection.run_sync(upgrade)
        for table, rows in before.items():
            assert (
                await connection.execute(text(f"SELECT * FROM {table}"))
            ).mappings().all() == rows


@pytest.mark.asyncio
async def test_t11_resend_identity_history_and_downgrade_cannot_be_bypassed(migrated_database):
    engine = migrated_database
    order, _, _ = await delivered_order(engine)
    service = resend_service(engine, RecordingSender(engine))
    action = await service.prepare(101, order.id)
    for sql in (
        "UPDATE resend_actions SET status='SENT'",
        "UPDATE resend_actions SET staff=true",
        "UPDATE resend_actions SET expires_at=expires_at+interval '1 minute'",
        "DELETE FROM resend_actions",
        "TRUNCATE resend_actions",
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(text(sql))
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.run_sync(downgrade)
    assert (await service.confirm(101, action.id)).status == "SENT"
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE resend_actions SET status='SENDING'"))
