from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import (
    DeliveryCode,
    DeliveryResult,
    DeliveryState,
    ManualNotice,
)
from telebotshop.domain.models.resources import ReusableTextPayload
from tests.fulfillment import fulfillment_service
from tests.orders import order_service
from tests.payments import payment_service, receipt
from tests.resources import resource_product, resource_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,document",
    [
        (ProductType.DIGITAL_FILE, False),
        (ProductType.REUSABLE_RESOURCE, False),
        (ProductType.REUSABLE_RESOURCE, True),
        (ProductType.MANUAL_FULFILLMENT, False),
    ],
)
async def test_resource_worker_sends_committed_entitlement_and_manual_waits_for_staff(
    migrated_database, kind, document
):
    engine = migrated_database
    product, version = await resource_product(engine, kind, document=document)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payment_service(engine).record(receipt(order))

    async def send(request):
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT state FROM fulfillments")) == "SENDING"
            assert (
                await connection.scalar(text("SELECT resource_id FROM fulfillments")) == version.id
            )
        assert request.context.resource_id == version.id and request.destination_telegram_id == 101
        if kind == ProductType.MANUAL_FULFILLMENT:
            assert isinstance(request.payload, ManualNotice)
            assert not hasattr(request.payload, "staff_instructions")
        return DeliveryResult(DeliveryState.SENT, message_id=99)

    sender = AsyncMock()
    sender.send.side_effect = send
    service = fulfillment_service(engine, sender)
    assert await service.run_once() is True
    assert await service.run_once() is False
    assert sender.send.await_count == 1
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == (
            "AWAITING_MANUAL" if kind == ProductType.MANUAL_FULFILLMENT else "SENT"
        )
        assert await connection.scalar(text("SELECT status FROM orders")) == (
            "FULFILLING" if kind == ProductType.MANUAL_FULFILLMENT else "DELIVERED"
        )
        assert await connection.scalar(text("SELECT state FROM delivery_attempts")) == "SENT"


@pytest.mark.asyncio
async def test_product_resource_edit_keeps_old_order_payload_snapshot(migrated_database):
    engine = migrated_database
    product, first = await resource_product(engine, ProductType.REUSABLE_RESOURCE)
    order = await order_service(engine).create(101, product.id, uuid4())
    second = await resource_service(engine).prepare(
        9001, "DEMO-1", ReusableTextPayload("FAKE replacement")
    )
    await resource_service(engine).confirm(9001, second.id)
    await payment_service(engine).record(receipt(order))
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.SENT, message_id=5)
    assert await fulfillment_service(engine, sender).run_once()
    sent = sender.send.await_args.args[0]
    assert sent.context.resource_id == first.id and sent.payload.text != "FAKE replacement"
    new_order = await order_service(engine).create(102, product.id, uuid4())
    await payment_service(engine).record(receipt(new_order, payer=102, charge="fake-new-charge"))
    assert await fulfillment_service(engine, sender).run_once()
    assert sender.send.await_args.args[0].context.resource_id == second.id


@pytest.mark.asyncio
async def test_manual_network_uncertainty_does_not_become_completed_or_retry(migrated_database):
    engine = migrated_database
    product, _ = await resource_product(engine, ProductType.MANUAL_FULFILLMENT)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payment_service(engine).record(receipt(order))
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.UNCERTAIN, code=DeliveryCode.NETWORK)
    service = fulfillment_service(engine, sender)
    assert await service.run_once()
    assert not await service.run_once()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "UNCERTAIN"
        assert await connection.scalar(text("SELECT status FROM orders")) == "FULFILLING"
