from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.fulfillment import fulfillment_service, paid_work
from tests.integration.test_migrations import upgrade


@pytest.mark.asyncio
async def test_t09_upgrade_preserves_paid_receipt_allocation_ciphertext_and_ready_work(database):
    engine = database
    async with engine.begin() as connection:
        await connection.run_sync(upgrade, "0008_payments")
    order, work_id = await paid_work(engine)
    tables = (
        "users",
        "orders",
        "order_items",
        "invoice_intents",
        "payments",
        "inventory_units",
        "fulfillments",
        "inventory_crypto_state",
        "audit_logs",
    )
    async with engine.begin() as connection:
        before = {
            table: (await connection.execute(text(f"SELECT * FROM {table}"))).mappings().all()
            for table in tables
        }
        await connection.run_sync(upgrade)
        for table, rows in before.items():
            columns = ", ".join(rows[0])
            after = (
                (await connection.execute(text(f"SELECT {columns} FROM {table}"))).mappings().all()
            )
            assert [dict(row) for row in after] == [dict(row) for row in rows]
        assert await connection.scalar(text("SELECT count(*) FROM delivery_attempts")) == 0
        assert (
            await connection.execute(
                text("SELECT send_count, hold, active_attempt_id FROM fulfillments")
            )
        ).one() == (0, False, None)
    claim = await fulfillment_service(engine, AsyncMock()).claim()
    assert claim.order_id == order.id and claim.work_id == work_id


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed", [False, True])
async def test_t09_downgrade_preserves_untouched_ready_work_and_refuses_attempt_history(
    migrated_database,
    claimed,
):
    engine = migrated_database
    await paid_work(engine)
    if claimed:
        await fulfillment_service(engine, AsyncMock()).claim()

    def downgrade(connection):
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.downgrade(config, "0008_payments")

    if claimed:
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.run_sync(downgrade)
    else:
        async with engine.begin() as connection:
            await connection.run_sync(downgrade)
            assert await connection.scalar(text("SELECT status FROM orders")) == "PAID"
            assert await connection.scalar(text("SELECT state FROM fulfillments")) == "READY"
            await connection.run_sync(upgrade)
        assert await fulfillment_service(engine, AsyncMock()).claim() is not None
