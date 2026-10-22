from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.manual import manual_service, waiting_manual
from tests.orders import order_service
from tests.payments import payment_service, receipt
from tests.resources import resource_payload, resource_product, resource_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE resource_versions SET ciphertext = repeat('x', 32)::bytea",
        "UPDATE resource_versions SET product_version = product_version + 1",
        "DELETE FROM resource_versions",
        "TRUNCATE resource_versions CASCADE",
        "UPDATE order_resources SET resource_id = resource_id",
        "DELETE FROM order_resources",
        "TRUNCATE order_resources CASCADE",
    ],
)
async def test_encrypted_source_and_order_binding_are_immutable_in_postgresql(
    migrated_database, statement
):
    engine = migrated_database
    product, version = await resource_product(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(text(statement))
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT resource_id FROM order_resources")) == version.id
        )
    assert (await order_service(engine).get(101, order.id)).unit_id is None


@pytest.mark.asyncio
async def test_paid_work_cannot_be_redirected_to_another_published_resource(migrated_database):
    engine = migrated_database
    product, original = await resource_product(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payment_service(engine).record(receipt(order))
    replacement = await resource_service(engine).prepare(
        9001, "DEMO-1", resource_payload(product.draft.product_type)
    )
    await resource_service(engine).confirm(9001, replacement.id)
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE fulfillments SET resource_id = :id"), {"id": replacement.id}
            )
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT resource_id FROM fulfillments")) == original.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE fulfillments SET state = 'SENT'",
        "UPDATE manual_actions SET status = 'applied', completed_at = clock_timestamp()",
        "UPDATE manual_actions SET ciphertext = repeat('x', 32)::bytea",
        "DELETE FROM manual_actions",
        "TRUNCATE manual_actions CASCADE",
    ],
)
async def test_manual_completion_cannot_skip_evidence_or_atomic_order_transition(
    migrated_database, statement
):
    engine = migrated_database
    _, work_id = await waiting_manual(engine)
    await manual_service(engine).prepare_completion(9001, work_id, "FAKE evidence")
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(text(statement))
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "AWAITING_MANUAL"
        assert await connection.scalar(text("SELECT status FROM orders")) == "FULFILLING"
        assert await connection.scalar(text("SELECT status FROM manual_actions")) == "pending"
