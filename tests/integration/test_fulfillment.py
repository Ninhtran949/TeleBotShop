from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from telebotshop.domain.models.fulfillment import DeliveryResult, DeliveryState
from tests.fulfillment import fulfillment_service, paid_work


@pytest.mark.asyncio
async def test_wrong_payload_type_fails_before_sending_and_keeps_paid_entitlement(
    migrated_database,
):
    from unittest.mock import Mock

    from telebotshop.domain.models.inventory import LicensePayload

    engine = migrated_database
    await paid_work(engine)
    sender = AsyncMock()
    service = fulfillment_service(engine, sender)
    service.cipher = Mock(open=Mock(return_value=LicensePayload("FAKE-wrong-type")))
    claim = await service.claim()
    assert await service.prepare(claim) is None
    sender.send.assert_not_awaited()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "FAILED"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"


@pytest.mark.asyncio
async def test_committed_paid_work_sends_once_outside_transaction_and_keeps_allocation(
    migrated_database,
):
    engine = migrated_database
    order, work_id = await paid_work(engine)

    async def send(request):
        assert engine.pool.checkedout() == 0
        assert request.work_id == work_id and request.destination_telegram_id == 101
        assert request.context.unit_id == order.unit_id
        assert request.payload.key == "fake-key-0"
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT state FROM fulfillments")) == "SENDING"
            assert await connection.scalar(text("SELECT state FROM delivery_attempts")) == "SENDING"
        return DeliveryResult(DeliveryState.SENT, message_id=77)

    sender = AsyncMock()
    sender.send.side_effect = send
    service = fulfillment_service(engine, sender)
    assert await service.run_once() is True
    assert await service.run_once() is False
    sender.send.assert_awaited_once()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "DELIVERED"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
        assert (
            await connection.scalar(text("SELECT allocated_item_id FROM inventory_units"))
            == order.item_id
        )
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "SENT"
        assert await connection.scalar(text("SELECT message_id FROM delivery_attempts")) == 77


@pytest.mark.asyncio
async def test_claim_alone_cannot_decrypt_or_send_and_stale_token_cannot_prepare(migrated_database):
    from dataclasses import replace
    from uuid import uuid4

    engine = migrated_database
    await paid_work(engine)
    sender = AsyncMock()
    service = fulfillment_service(engine, sender)
    claim = await service.claim()
    assert claim is not None
    assert await service.claim() is None
    assert await service.prepare(replace(claim, attempt_id=uuid4())) is None
    sender.send.assert_not_awaited()
    assert await service.prepare(claim) is not None


@pytest.mark.asyncio
async def test_license_dispatch_uses_its_allocated_typed_secret(migrated_database):
    from telebotshop.domain.models.catalog import ProductType

    engine = migrated_database
    order, _ = await paid_work(engine, kind=ProductType.LICENSE)
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.SENT, message_id=12)
    service = fulfillment_service(engine, sender)
    assert await service.run_once() is True
    request = sender.send.await_args.args[0]
    assert request.payload.license_key == "FAKE-license-0"
    assert request.context.kind == ProductType.LICENSE and request.context.unit_id == order.unit_id
