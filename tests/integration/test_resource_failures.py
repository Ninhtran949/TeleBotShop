from dataclasses import replace
from functools import partial
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from telebotshop.application.services.fulfillment import FulfillmentService
from telebotshop.domain.errors import InvalidAction, InvalidInput
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import DeliveryPolicy, DeliveryResult, DeliveryState
from telebotshop.domain.models.resources import (
    DigitalFilePayload,
    ManualPayload,
    ReusableDocumentPayload,
)
from telebotshop.infrastructure.database.uow import SqlAlchemyUnitOfWork, unit_of_work
from tests.fulfillment import fulfillment_service
from tests.inventory import fake_cipher
from tests.orders import order_service
from tests.payments import payment_service, receipt
from tests.resources import FILE_ID, resource_payload, resource_product, resource_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,payload",
    [
        (ProductType.DIGITAL_FILE, DigitalFilePayload(FILE_ID, "wrong.pdf", "application/pdf")),
        (ProductType.REUSABLE_RESOURCE, ReusableDocumentPayload(FILE_ID)),
        (ProductType.MANUAL_FULFILLMENT, ManualPayload("Wrong public notice", "FAKE staff")),
    ],
)
async def test_resource_registration_rejects_metadata_or_format_mismatch(
    migrated_database, kind, payload
):
    await resource_product(migrated_database, kind, publish=False)
    with pytest.raises(InvalidInput):
        await resource_service(migrated_database).prepare(9001, "DEMO-1", payload)
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM resource_versions")) == 0


@pytest.mark.asyncio
async def test_cancelled_or_catalog_stale_resource_action_cannot_publish(migrated_database):
    from telebotshop.application.services.catalog import CatalogService

    engine = migrated_database
    await resource_product(engine, publish=False)
    service = resource_service(engine)
    cancelled = await service.prepare(9001, "DEMO-1", resource_payload(ProductType.DIGITAL_FILE))
    await service.cancel(9001, cancelled.id)
    await service.cancel(9001, cancelled.id)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, cancelled.id)
    stale = await service.prepare(9001, "DEMO-1", resource_payload(ProductType.DIGITAL_FILE))
    catalog = CatalogService(partial(unit_of_work, engine))
    action = await catalog.prepare_price(9001, "DEMO-1", 200)
    await catalog.confirm(9001, action.id)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, stale.id)
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM resource_versions WHERE state = 'ACTIVE'")
            )
            == 0
        )


@pytest.mark.asyncio
async def test_nonfinite_payment_commit_failure_rolls_back_and_redelivery_commits_one_work(
    migrated_database, monkeypatch
):
    engine = migrated_database
    product, version = await resource_product(engine)
    order = await order_service(engine).create(101, product.id, uuid4())

    async def fail(self):
        raise OperationalError("fake", {}, OSError("FAKE private DSN"))

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyUnitOfWork, "commit", fail)
        with pytest.raises(OperationalError):
            await payment_service(engine).record(receipt(order))
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM payments")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 0
        assert await connection.scalar(text("SELECT status FROM orders")) == "AWAITING_PAYMENT"
        assert (
            await connection.scalar(text("SELECT resource_id FROM order_resources")) == version.id
        )
    await payment_service(engine).record(receipt(order))
    await payment_service(engine).record(receipt(order))
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["aad", "wrong_format", "invalid_envelope"])
async def test_bad_resource_entitlement_fails_closed_before_send_and_keeps_paid_binding(
    migrated_database, corruption
):
    from contextlib import asynccontextmanager

    from telebotshop.domain.models.inventory import InventoryCryptoError

    engine = migrated_database
    product, version = await resource_product(engine, ProductType.REUSABLE_RESOURCE)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payment_service(engine).record(receipt(order))
    cipher = fake_cipher()
    if corruption == "wrong_format":
        cipher = Mock(wraps=cipher)
        cipher.open.return_value = ReusableDocumentPayload(FILE_ID)

    @asynccontextmanager
    async def factory():
        async with unit_of_work(engine) as work:
            original = work.fulfillment.entitlement

            async def altered(fulfillment):
                if corruption == "invalid_envelope":
                    raise InventoryCryptoError("FAKE corrupt envelope")
                value = await original(fulfillment)
                if corruption == "aad":
                    return replace(value, context=replace(value.context, resource_id=uuid4()))
                return value

            work.fulfillment.entitlement = altered
            yield work

    sender = AsyncMock()
    service = FulfillmentService(factory, cipher, sender, DeliveryPolicy())
    assert await service.run_once()
    sender.send.assert_not_awaited()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "FAILED"
        assert await connection.scalar(text("SELECT resource_id FROM fulfillments")) == version.id
        assert await connection.scalar(text("SELECT count(*) FROM payments")) == 1


@pytest.mark.asyncio
async def test_send_success_result_commit_failure_recovers_uncertain_without_another_file_send(
    migrated_database, monkeypatch
):
    engine = migrated_database
    product, _ = await resource_product(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payment_service(engine).record(receipt(order))
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.SENT, message_id=99)
    service = fulfillment_service(engine, sender)
    claim = await service.claim()
    await service.prepare(claim)

    async def fail(self):
        raise OperationalError("fake", {}, OSError("FAKE private DSN"))

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyUnitOfWork, "commit", fail)
        with pytest.raises(OperationalError):
            await service.complete(claim, await sender.send(AsyncMock()))
    from datetime import UTC, datetime, timedelta

    from tests.fulfillment import timed_uow

    recovery = FulfillmentService(
        timed_uow(engine, datetime.now(UTC) + timedelta(minutes=10)),
        fake_cipher(),
        sender,
        DeliveryPolicy(),
    )
    assert await recovery.recover() == 1
    assert not await recovery.run_once()
    assert sender.send.await_count == 1
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "UNCERTAIN"
