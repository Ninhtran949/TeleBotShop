from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.refunds import RefundResult, RefundState
from tests.fulfillment import fulfillment_service, paid_work
from tests.payments import payment_service, receipt
from tests.refunds import refund_service
from tests.resends import RecordingSender, delivered_order, resend_service


@pytest.mark.asyncio
async def test_primary_refund_commits_hold_before_api_and_keeps_paid_allocation(migrated_database):
    engine = migrated_database
    order, _ = await paid_work(engine)
    async with engine.connect() as connection:
        payment_id = await connection.scalar(text("SELECT id FROM payments"))
    gateway = AsyncMock()

    async def refund(request):
        assert engine.pool.checkedout() == 0
        assert request.payer_id == 101 and request.charge_id == receipt(order).charge_id
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT status FROM refund_intents")) == "SENDING"
            assert await connection.scalar(text("SELECT hold FROM fulfillments")) is True
        assert await fulfillment_service(engine, AsyncMock()).claim() is None
        return RefundResult(RefundState.SUCCEEDED)

    gateway.refund.side_effect = refund
    service = refund_service(engine, gateway)
    prepared = await service.prepare(9001, payment_id, "FAKE-policy-approved")
    assert (await service.prepare(9001, payment_id, "FAKE-edited-replay")).id == prepared.id
    assert (await service.confirm(9001, prepared.id)).status == RefundState.SUCCEEDED
    assert (await service.confirm(9001, prepared.id)).status == RefundState.SUCCEEDED
    gateway.refund.assert_awaited_once()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "REFUNDED"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "READY"
        assert await connection.scalar(text("SELECT count(*) FROM delivery_attempts")) == 0
        assert "FAKE-policy-approved" not in str(
            (await connection.execute(text("SELECT * FROM refund_intents"))).all()
        )
        assert "FAKE-policy-approved" not in str(
            (await connection.execute(text("SELECT * FROM audit_logs"))).all()
        )


@pytest.mark.asyncio
async def test_unauthorized_refund_and_cross_actor_confirm_fail_before_api(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    service = refund_service(engine)
    async with engine.connect() as connection:
        payment_id = await connection.scalar(text("SELECT id FROM payments"))
    with pytest.raises(AccessDenied):
        await service.prepare(101, payment_id, "FAKE-denied")
    action = await service.prepare(9001, payment_id, "FAKE-approved")
    with pytest.raises(AccessDenied):
        await service.confirm(101, action.id)
    service.gateway.refund.assert_not_awaited()


@pytest.mark.asyncio
async def test_extra_refund_requires_verified_transaction_and_preserves_primary(migrated_database):
    from datetime import UTC, datetime

    from telebotshop.domain.models.refunds import StarEvidence

    engine = migrated_database
    order, _ = await paid_work(engine)
    extra = replace(receipt(order), charge_id="FAKE-extra-charge")
    result = await payment_service(engine).record(extra)
    service = refund_service(engine)
    with pytest.raises(InvalidAction):
        await service.prepare(9001, result.payment_id, "FAKE-not-verified")
    service.gateway.transactions.return_value = (
        StarEvidence(extra.charge_id, 101, extra.amount, extra.payload, False, datetime.now(UTC)),
    )
    assert await service.verify(9001, result.payment_id) is True
    prepared = await service.prepare(9001, result.payment_id, "FAKE-extra-approved")
    assert (await service.confirm(9001, prepared.id)).status == RefundState.SUCCEEDED
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "PAID"
        assert await connection.scalar(text("SELECT hold FROM fulfillments")) is False
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ProductType))
async def test_native_refund_blocks_existing_entitlement_reader_for_every_type_without_reallocation(
    migrated_database, kind
):
    engine = migrated_database
    order, _, _ = await delivered_order(engine, kind)
    async with engine.connect() as connection:
        payment = await connection.scalar(text("SELECT id FROM payments"))
        material = (
            await connection.execute(
                text(
                    "SELECT "
                    "inventory_unit_id,resource_id,send_count,active_attempt_id FROM "
                    "fulfillments"
                )
            )
        ).one()
        attempts = (await connection.execute(text("SELECT * FROM delivery_attempts"))).all()
    service = refund_service(engine)
    action = await service.prepare(9001, payment, "FAKE-full-refund-policy-decision")
    await service.confirm(9001, action.id)
    with pytest.raises(InvalidAction):
        await resend_service(engine, RecordingSender(engine)).prepare(101, order.id)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "REFUNDED"
        assert (
            await connection.execute(
                text(
                    "SELECT "
                    "inventory_unit_id,resource_id,send_count,active_attempt_id FROM "
                    "fulfillments"
                )
            )
        ).one() == material
        assert (await connection.execute(text("SELECT * FROM delivery_attempts"))).all() == attempts


@pytest.mark.asyncio
async def test_late_paid_attention_without_work_can_refund_without_touching_released_stock(
    migrated_database,
):
    from uuid import uuid4

    from tests.orders import order_service, setup_orders

    engine = migrated_database
    product = await setup_orders(engine)
    orders = order_service(engine)
    order = await orders.create(101, product.id, uuid4())
    await orders.cancel(101, order.id)
    result = await payment_service(engine).record(receipt(order))
    assert result.attention_reason is not None
    service = refund_service(engine)
    action = await service.prepare(9001, result.payment_id, "FAKE-late-payment-full-refund")
    assert (await service.confirm(9001, action.id)).status == RefundState.SUCCEEDED
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "REFUNDED"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "AVAILABLE"
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 0
