from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telebotshop.domain.models.fulfillment import DeliveryCode, DeliveryResult, DeliveryState
from telebotshop.domain.models.refunds import RefundReceipt, StarEvidence
from telebotshop.domain.models.support import TicketKind
from telebotshop.infrastructure.database.models.refunds import metadata
from telebotshop.infrastructure.database.schema import SCHEMA_HEAD
from tests.fulfillment import paid_work
from tests.integration.test_migrations import upgrade
from tests.integration.test_resource_migrations import legacy_delivery
from tests.payments import payment_service, receipt
from tests.refunds import refund_service
from tests.resends import RecordingSender, delivered_order, resend_service
from tests.support import support_service


def downgrade(connection):
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0012_support")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        "READY",
        "CLAIMED",
        "SENDING",
        "SENT",
        "RETRY_WAIT",
        "UNCERTAIN",
        "FAILED",
        "RESEND_PENDING",
        "RESEND_SENDING",
        "RESEND_SENT",
        "RESEND_UNCERTAIN",
    ],
)
async def test_t13_upgrade_downgrade_preserve_t12_history_and_predecessor_functions(
    database, state
):
    engine = database
    async with engine.begin() as connection:
        await connection.run_sync(upgrade, "0012_support")
    if state.startswith("RESEND_"):
        order, _, _ = await delivered_order(engine, pre_refunds=True)
        result = (
            DeliveryResult(DeliveryState.UNCERTAIN, code=DeliveryCode.NETWORK)
            if state == "RESEND_UNCERTAIN"
            else None
        )
        resends = resend_service(engine, RecordingSender(engine, result))
        action = await resends.prepare(101, order.id)
        if state == "RESEND_SENDING":
            await resends.begin(101, action.id)
        elif state != "RESEND_PENDING":
            await resends.confirm(101, action.id)
    else:
        order, _ = await legacy_delivery(engine, state)
    ticket = await support_service(engine).create(
        101, TicketKind.PAYMENT, "FAKE private ticket", uuid4(), order_id=order.id
    )
    tables = [
        "users",
        "orders",
        "order_items",
        "payments",
        "fulfillments",
        "inventory_units",
        "delivery_attempts",
        "resend_actions",
        "support_tickets",
        "support_messages",
        "audit_logs",
    ]
    async with engine.begin() as connection:
        before = {
            table: (await connection.execute(text(f"SELECT * FROM {table}"))).mappings().all()
            for table in tables
        }
        functions = (
            await connection.execute(
                text(
                    "SELECT proname,pg_get_functiondef(oid) FROM pg_proc WHERE "
                    "pronamespace=current_schema()::regnamespace ORDER BY proname"
                )
            )
        ).all()
        await connection.run_sync(upgrade)
        assert (
            await connection.run_sync(
                lambda c: compare_metadata(MigrationContext.configure(c), metadata)
            )
            == []
        )
        await connection.run_sync(downgrade)
        assert (
            await connection.execute(
                text(
                    "SELECT proname,pg_get_functiondef(oid) FROM pg_proc WHERE "
                    "pronamespace=current_schema()::regnamespace ORDER BY proname"
                )
            )
        ).all() == functions
        await connection.run_sync(upgrade)
        for table, rows in before.items():
            columns = ", ".join(rows[0]) if rows else "*"
            assert (
                await connection.execute(text(f"SELECT {columns} FROM {table}"))
            ).mappings().all() == rows
        assert await connection.scalar(text("SELECT refund_intent_id FROM fulfillments")) is None
    assert (await support_service(engine).detail(101, ticket.id)).messages.items[
        0
    ].text == "FAKE private ticket"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["intent", "receipt", "verification"])
async def test_t13_downgrade_refuses_refund_or_verification_history(migrated_database, kind):
    engine = migrated_database
    order, _ = await paid_work(engine)
    service = refund_service(engine)
    async with engine.connect() as connection:
        payment = await connection.scalar(text("SELECT id FROM payments"))
    if kind == "intent":
        await service.prepare(9001, payment, "FAKE-policy-reference")
    elif kind == "receipt":
        await service.record(RefundReceipt("FAKE-unknown", "XTR", 12, "FAKE-payload"))
    else:
        extra = replace(receipt(order), charge_id="FAKE-extra")
        stored = await payment_service(engine).record(extra)
        service.gateway.transactions.return_value = (
            StarEvidence(
                extra.charge_id, 101, extra.amount, extra.payload, False, datetime.now(UTC)
            ),
        )
        assert await service.verify(9001, stored.payment_id)
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.run_sync(downgrade)
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_HEAD
        )
        assert await connection.scalar(text("SELECT to_regclass('notifications')")) is not None
