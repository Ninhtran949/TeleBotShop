from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telebotshop.domain.models.fulfillment import DeliveryCode, DeliveryResult, DeliveryState
from telebotshop.domain.models.support import TicketKind
from tests.integration.test_migrations import upgrade
from tests.resends import RecordingSender, delivered_order, resend_service
from tests.support import support_service


def downgrade(connection):
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0011_history")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PENDING", "SENDING", "SENT", "UNCERTAIN"])
async def test_t12_upgrade_empty_downgrade_preserve_t11_paid_entitlement_and_resend(
    database, state
):
    engine = database
    async with engine.begin() as connection:
        await connection.run_sync(upgrade, "0011_history")
    order, _, _ = await delivered_order(engine)
    result = (
        DeliveryResult(DeliveryState.UNCERTAIN, code=DeliveryCode.NETWORK)
        if state == "UNCERTAIN"
        else None
    )
    service = resend_service(engine, RecordingSender(engine, result))
    action = await service.prepare(101, order.id)
    if state == "SENDING":
        await service.begin(101, action.id)
    elif state != "PENDING":
        await service.confirm(101, action.id)
    tables = (
        "orders",
        "order_items",
        "payments",
        "fulfillments",
        "inventory_units",
        "delivery_attempts",
        "resend_actions",
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
async def test_t12_downgrade_preserves_support_history_by_refusing_it(migrated_database):
    engine = migrated_database
    order, _, _ = await delivered_order(engine)
    ticket = await support_service(engine).create(
        101, TicketKind.PAYMENT, "FAKE issue", uuid4(), order_id=order.id
    )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.run_sync(downgrade)
    assert (await support_service(engine).detail(101, ticket.id)).messages.items[
        0
    ].text == "FAKE issue"
