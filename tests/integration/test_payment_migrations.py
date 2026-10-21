from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_migrations import upgrade
from tests.orders import historical_order, order_service, setup_orders
from tests.payments import payment_service, receipt
from tests.pre_checkout import payable_invoice


@pytest.mark.asyncio
async def test_t08_upgrade_preserves_all_t07_rows_and_approval_then_accepts_payment(database):
    engine = database
    async with engine.begin() as connection:
        await connection.run_sync(upgrade, "0007_pre_checkout")
    product = await setup_orders(engine)
    order = await historical_order(engine, product)
    await payable_invoice(engine, order)
    tables = (
        "users",
        "products",
        "inventory_units",
        "inventory_crypto_state",
        "orders",
        "order_items",
        "invoice_intents",
        "pre_checkout_approvals",
        "audit_logs",
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO pre_checkout_approvals(order_id, query_id, approved_at) "
                "SELECT id, 'FAKE-migration-query', created_at FROM orders"
            )
        )
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
        assert await connection.scalar(text("SELECT count(*) FROM payments")) == 0
    assert (await payment_service(engine).record(receipt(order))).attention_reason is None


def downgrade(connection):
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0007_pre_checkout")


@pytest.mark.asyncio
@pytest.mark.parametrize("received", [False, True])
async def test_t08_downgrade_preserves_unpaid_data_and_blocks_financial_history(
    migrated_database,
    received,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    if received:
        await payment_service(engine).record(receipt(order))
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.run_sync(downgrade)
        assert (await payment_service(engine).record(receipt(order))).outcome == "DUPLICATE"
    else:
        async with engine.begin() as connection:
            before = (await connection.execute(text("SELECT * FROM invoice_intents"))).one()
            await connection.run_sync(downgrade)
            assert await connection.scalar(text("SELECT status FROM inventory_units")) == "RESERVED"
            assert (await connection.execute(text("SELECT * FROM invoice_intents"))).one() == before
            await connection.run_sync(upgrade)
        assert (await payment_service(engine).record(receipt(order))).outcome == "MATCHED"
