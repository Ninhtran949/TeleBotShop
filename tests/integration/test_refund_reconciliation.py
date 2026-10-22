from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.fulfillment import DeliveryResult, DeliveryState
from telebotshop.domain.models.refunds import RefundReceipt, RefundResult, RefundState, StarEvidence
from tests.fulfillment import fulfillment_service, historical_claim, paid_work
from tests.orders import order_service, setup_orders
from tests.payments import payment_service, receipt
from tests.refunds import refund_service


async def payment_id(engine):
    async with engine.connect() as connection:
        return await connection.scalar(text("SELECT id FROM payments LIMIT 1"))


@pytest.mark.asyncio
async def test_known_rejection_releases_only_owned_hold_and_requires_new_confirmation(
    migrated_database,
):
    engine = migrated_database
    await paid_work(engine)
    service = refund_service(engine)
    service.gateway.refund.return_value = RefundResult(RefundState.FAILED)
    prepared = await service.prepare(9001, await payment_id(engine), "FAKE-approved")
    assert (await service.confirm(9001, prepared.id)).status == RefundState.FAILED
    assert (await service.confirm(9001, prepared.id)).status == RefundState.FAILED
    service.gateway.refund.assert_awaited_once()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT hold FROM fulfillments")) is False
        assert await connection.scalar(text("SELECT refund_intent_id FROM fulfillments")) is None
        assert await connection.scalar(text("SELECT status FROM orders")) == "PAID"
    retry = await service.prepare(9001, prepared.payment_id, "FAKE-new-approved-request")
    assert retry.id != prepared.id
    service.gateway.refund.return_value = RefundResult(RefundState.SUCCEEDED)
    assert (await service.confirm(9001, retry.id)).status == RefundState.SUCCEEDED


@pytest.mark.asyncio
async def test_uncertain_refund_requires_positive_matching_outbound_evidence_without_resubmit(
    migrated_database,
):
    engine = migrated_database
    order, _ = await paid_work(engine)
    service = refund_service(engine)
    service.gateway.refund.return_value = RefundResult(RefundState.UNCERTAIN)
    prepared = await service.prepare(9001, await payment_id(engine), "FAKE-approved")
    assert (await service.confirm(9001, prepared.id)).status == RefundState.UNCERTAIN
    assert (await service.confirm(9001, prepared.id)).status == RefundState.UNCERTAIN
    with pytest.raises(InvalidAction):
        await service.prepare(9001, prepared.payment_id, "FAKE-blind-retry")
    assert await service.reconcile(9001, prepared.payment_id) is False
    evidence = StarEvidence(
        receipt(order).charge_id, 999, order.price_stars, None, True, datetime.now(UTC)
    )
    service.gateway.transactions.return_value = (evidence,)
    assert await service.reconcile(9001, prepared.payment_id) is False
    service.gateway.transactions.return_value = (replace(evidence, payer_id=101),)
    assert await service.reconcile(9001, prepared.payment_id) is True
    assert (await service.detail(9001, prepared.payment_id)).state == RefundState.SUCCEEDED
    service.gateway.refund.assert_awaited_once()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "REFUNDED"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"


@pytest.mark.asyncio
async def test_refund_receipt_before_payment_is_durable_and_later_intake_never_opens_delivery(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, __import__("uuid").uuid4())
    paid = receipt(order)
    event = RefundReceipt(
        paid.charge_id, paid.currency, paid.amount, paid.payload, paid.provider_charge_id
    )
    service = refund_service(engine)
    assert await service.record(event) is None
    assert await service.record(event) is None
    result = await payment_service(engine).record(paid)
    await service.record(event)
    await payment_service(engine).record(paid)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_receipts")) == 1
        assert await connection.scalar(text("SELECT status FROM orders")) == "REFUNDED"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
        assert await connection.scalar(text("SELECT hold FROM fulfillments")) is True
        assert await connection.scalar(text("SELECT count(*) FROM refund_intents")) == 1
    assert await fulfillment_service(engine, AsyncMock()).claim() is None
    assert (await service.detail(9001, result.payment_id)).state == RefundState.SUCCEEDED
    service.gateway.refund.assert_not_awaited()


@pytest.mark.asyncio
async def test_mismatched_refund_is_preserved_but_valid_external_refund_fences_in_flight_delivery(
    migrated_database,
):
    engine = migrated_database
    order, _ = await paid_work(engine)
    service = refund_service(engine)
    paid = receipt(order)
    invalid = RefundReceipt(paid.charge_id, "XTR", paid.amount + 1, paid.payload)
    assert await service.record(invalid) is None
    worker = fulfillment_service(engine, AsyncMock())
    claim = await worker.claim()
    assert await worker.prepare(claim) is not None
    event = replace(invalid, amount=paid.amount)
    assert await service.record(event) is not None
    assert await worker.complete(claim, DeliveryResult(DeliveryState.SENT, message_id=88)) is False
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "REFUNDED"
        assert await connection.scalar(text("SELECT hold FROM fulfillments")) is True
        assert await connection.scalar(text("SELECT count(*) FROM delivery_observations")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM refund_receipts")) == 2


@pytest.mark.asyncio
async def test_uncertain_delivery_requires_explicit_evidence_decision_and_preserves_attempt(
    migrated_database,
):
    engine = migrated_database
    order, claim, _ = await historical_claim(engine, sending=True)
    service = refund_service(engine)
    with pytest.raises(InvalidAction):
        await service.prepare(9001, await payment_id(engine), "FAKE-no-override")
    action = await service.prepare(
        9001,
        await payment_id(engine),
        "FAKE-operator-accepts-possible-delivery",
        accept_uncertain=True,
    )
    assert (await service.confirm(9001, action.id)).status == RefundState.SUCCEEDED
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT state FROM delivery_attempts WHERE id=:id"), {"id": claim.attempt_id}
            )
            == "UNCERTAIN"
        )
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "UNCERTAIN"
        assert await connection.scalar(text("SELECT status FROM orders")) == "REFUNDED"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
