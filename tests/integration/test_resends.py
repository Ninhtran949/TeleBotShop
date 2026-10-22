from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import DeliveryCode, DeliveryResult, DeliveryState
from tests.fulfillment import paid_work
from tests.resends import RecordingSender, delivered_order, resend_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,document",
    [
        (ProductType.UNIQUE_KEY, False),
        (ProductType.LICENSE, False),
        (ProductType.DIGITAL_FILE, False),
        (ProductType.REUSABLE_RESOURCE, False),
        (ProductType.REUSABLE_RESOURCE, True),
    ],
)
async def test_resend_keeps_exact_entitlement_and_primary_delivery(
    migrated_database, kind, document
):
    engine = migrated_database
    order, work_id, original = await delivered_order(engine, kind, document=document)
    sender = RecordingSender(engine)
    service = resend_service(engine, sender)
    action = await service.prepare(101, order.id)
    assert (await service.prepare(101, order.id)).id == action.id
    assert not sender.requests
    assert (await service.confirm(101, action.id)).status == "SENT"
    assert (await service.confirm(101, action.id)).status == "SENT"
    assert len(sender.requests) == 1
    resent = sender.requests[0]
    assert resent.work_id == work_id and resent.destination_telegram_id == 101
    assert resent.context == original.context and resent.payload == original.payload
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM delivery_attempts")) == 1
        assert await connection.scalar(text("SELECT status FROM orders")) == "DELIVERED"
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "SENT"
        assert await connection.scalar(text("SELECT count(*) FROM resend_actions")) == 1


@pytest.mark.asyncio
async def test_resend_scopes_cancellation_and_confirmation_replay(migrated_database):
    engine = migrated_database
    order, _, _ = await delivered_order(engine)
    sender = RecordingSender(engine)
    service = resend_service(engine, sender)
    for actor, staff in ((102, False), (101, True), (102, True)):
        with pytest.raises(AccessDenied):
            await service.prepare(actor, order.id, staff=staff)
    action = await service.prepare(101, order.id)
    with pytest.raises(AccessDenied):
        await service.confirm(102, action.id)
    with pytest.raises(AccessDenied):
        await service.confirm(101, uuid4())
    await service.cancel(101, action.id)
    assert (await service.confirm(101, action.id)).status == "CANCELLED"
    assert not sender.requests
    other = await service.prepare(9001, order.id, staff=True)
    assert other.id != action.id
    assert (await service.confirm(9001, other.id)).status == "SENT"
    assert sender.requests[0].destination_telegram_id == 101


@pytest.mark.asyncio
async def test_unpaid_and_manual_orders_do_not_allow_customer_resend(migrated_database):
    engine = migrated_database
    order, _ = await paid_work(engine)
    service = resend_service(engine, RecordingSender(engine))
    with pytest.raises(InvalidAction):
        await service.prepare(101, order.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        DeliveryResult(DeliveryState.UNCERTAIN, code=DeliveryCode.NETWORK),
        DeliveryResult(DeliveryState.RETRYABLE, code=DeliveryCode.RATE_LIMIT, retry_after=3),
        DeliveryResult(DeliveryState.FAILED, code=DeliveryCode.REJECTED),
    ],
)
async def test_resend_failure_is_durable_and_never_automatically_retries(migrated_database, result):
    engine = migrated_database
    order, _, _ = await delivered_order(engine)
    sender = RecordingSender(engine, result)
    service = resend_service(engine, sender)
    action = await service.prepare(101, order.id)
    expected = "UNCERTAIN" if result.state == DeliveryState.UNCERTAIN else "FAILED"
    assert (await service.confirm(101, action.id)).status == expected
    assert (await service.confirm(101, action.id)).status == expected
    assert len(sender.requests) == 1
    if expected == "UNCERTAIN":
        with pytest.raises(InvalidAction):
            await service.prepare(101, order.id)
