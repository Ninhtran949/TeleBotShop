from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.fulfillment import paid_work
from tests.integration.test_migrations import upgrade
from tests.resources import resource_product


async def legacy_delivery(engine, state):
    order, work_id = await paid_work(engine)
    if state == "READY":
        return order, work_id
    attempt_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("""INSERT INTO delivery_attempts
            (id, work_id, state, claimed_at, lease_expires_at)
            VALUES (:id, :work, 'CLAIMED', clock_timestamp(),
                clock_timestamp() + interval '60 seconds')"""),
            {"id": attempt_id, "work": work_id},
        )
        await connection.execute(
            text("UPDATE fulfillments SET state = 'CLAIMED', active_attempt_id = :id"),
            {"id": attempt_id},
        )
        if state != "CLAIMED":
            await connection.execute(
                text("""UPDATE delivery_attempts SET state = 'SENDING',
                sending_at = clock_timestamp(),
                lease_expires_at = clock_timestamp() + interval '60 seconds'""")
            )
            await connection.execute(
                text("UPDATE fulfillments SET state = 'SENDING', send_count = 1")
            )
            await connection.execute(text("UPDATE orders SET status = 'FULFILLING'"))
            if state == "SENT":
                await connection.execute(
                    text("""UPDATE delivery_attempts SET state = 'SENT',
                    completed_at = clock_timestamp(), message_id = 99""")
                )
                await connection.execute(text("UPDATE fulfillments SET state = 'SENT'"))
                await connection.execute(text("UPDATE orders SET status = 'DELIVERED'"))
            elif state in {"UNCERTAIN", "FAILED", "RETRY_WAIT"}:
                attempt_state = "RETRYABLE" if state == "RETRY_WAIT" else state
                code = {"UNCERTAIN": "NETWORK", "FAILED": "REJECTED", "RETRY_WAIT": "RATE_LIMIT"}[
                    state
                ]
                await connection.execute(
                    text("""UPDATE delivery_attempts SET state = :state,
                    completed_at = clock_timestamp(), error_code = :code, retry_after = :retry"""),
                    {
                        "state": attempt_state,
                        "code": code,
                        "retry": 2 if state == "RETRY_WAIT" else None,
                    },
                )
                await connection.execute(
                    text("UPDATE fulfillments SET state = :state, attention_code = :code"),
                    {"state": state, "code": None if state == "RETRY_WAIT" else code},
                )
                if state == "FAILED":
                    await connection.execute(text("UPDATE orders SET status = 'FAILED'"))
    return order, work_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["READY", "CLAIMED", "SENDING", "SENT", "UNCERTAIN", "FAILED", "RETRY_WAIT"]
)
async def test_t10_upgrade_preserves_existing_finite_payment_delivery_and_ciphertext(
    database, state
):
    engine = database
    async with engine.begin() as connection:
        await connection.run_sync(upgrade, "0009_fulfillment")
    await legacy_delivery(engine, state)
    tables = (
        "users",
        "orders",
        "order_items",
        "invoice_intents",
        "payments",
        "inventory_units",
        "fulfillments",
        "delivery_attempts",
        "delivery_observations",
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
            if rows:
                columns = ", ".join(rows[0])
                after = (
                    (await connection.execute(text(f"SELECT {columns} FROM {table}")))
                    .mappings()
                    .all()
                )
                assert [dict(row) for row in after] == [dict(row) for row in rows]
        assert await connection.scalar(text("SELECT resource_id FROM fulfillments")) is None
        assert await connection.scalar(text("SELECT count(*) FROM resource_versions")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM manual_actions")) == 0
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM user_permissions WHERE permission = 'fulfillment.manage'"
                )
            )
            == 0
        )


def downgrade_to_t09(connection):
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0009_fulfillment")


@pytest.mark.asyncio
async def test_t10_safe_downgrade_preserves_finite_history_and_can_upgrade_again(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    async with engine.begin() as connection:
        await connection.run_sync(downgrade_to_t09)
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "READY"
        assert await connection.scalar(text("SELECT status FROM orders")) == "PAID"
        await connection.run_sync(upgrade)
        assert await connection.scalar(text("SELECT count(*) FROM payments")) == 1


@pytest.mark.asyncio
async def test_t10_downgrade_refuses_to_erase_resource_or_confirmation_history(migrated_database):
    engine = migrated_database
    await resource_product(engine)
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.run_sync(downgrade_to_t09)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM resource_versions")) == 1
