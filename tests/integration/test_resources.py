from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.domain.errors import AccessDenied, CatalogNotFound, InvalidAction, InvalidInput
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.resources import ReusableTextPayload
from tests.orders import order_service
from tests.payments import payment_service, receipt
from tests.pre_checkout import checkout_request, payable_invoice
from tests.resources import resource_payload, resource_product, resource_service


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
async def test_nonfinite_checkout_payment_uses_pinned_encrypted_resource_without_stock(
    migrated_database, kind, document
):
    engine = migrated_database
    product, version = await resource_product(engine, kind, document=document)
    service = order_service(engine)
    action_id = uuid4()
    order = await service.create(101, product.id, action_id)
    assert order.unit_id is None
    assert await service.create(101, product.id, action_id) == order
    await payable_invoice(engine, order)
    from telebotshop.application.services.pre_checkout import PreCheckoutService

    assert await PreCheckoutService(service.uow).check(checkout_request(order)) is True
    result = await payment_service(engine).record(receipt(order))
    assert result.attention_reason is None
    duplicate = await payment_service(engine).record(receipt(order))
    assert duplicate.payment_id == result.payment_id
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM inventory_units")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1
        assert await connection.scalar(text("SELECT resource_id FROM fulfillments")) == version.id
        assert (
            await connection.scalar(text("SELECT resource_id FROM order_resources")) == version.id
        )
        assert await connection.scalar(text("SELECT status FROM orders")) == "PAID"
        encrypted = await connection.scalar(text("SELECT ciphertext FROM resource_versions"))
        assert b"FAKE" not in encrypted
        audit = str((await connection.execute(text("SELECT changes FROM audit_logs"))).all())
        assert "FAKE_registered" not in audit and "FAKE reusable" not in audit


@pytest.mark.asyncio
async def test_unpublished_source_not_purchasable_and_cancel_has_no_inventory_side_effect(
    migrated_database,
):
    engine = migrated_database
    product, _ = await resource_product(engine, publish=False)
    service = order_service(engine)
    with pytest.raises(CatalogNotFound):
        await service.create(101, product.id, uuid4())
    action = await resource_service(engine).prepare(
        9001, "DEMO-1", resource_payload(product.draft.product_type)
    )
    with pytest.raises(CatalogNotFound):
        await service.create(101, product.id, uuid4())
    await resource_service(engine).confirm(9001, action.id)
    order = await service.create(101, product.id, uuid4())
    assert (await service.cancel(101, order.id)).status == "EXPIRED"
    assert (await payment_service(engine).record(receipt(order))).attention_reason is None


@pytest.mark.asyncio
async def test_resource_registration_requires_current_permission_and_matching_payload(
    migrated_database,
):
    engine = migrated_database
    await resource_product(engine, publish=False)
    service = resource_service(engine)
    with pytest.raises(AccessDenied):
        await service.prepare(101, "DEMO-1", resource_payload(ProductType.DIGITAL_FILE))
    with pytest.raises(InvalidInput):
        await service.prepare(9001, "DEMO-1", ReusableTextPayload("FAKE wrong kind"))
    first = await service.prepare(9001, "DEMO-1", resource_payload(ProductType.DIGITAL_FILE))
    second = await service.prepare(9001, "DEMO-1", resource_payload(ProductType.DIGITAL_FILE))
    await service.confirm(9001, first.id)
    assert await service.confirm(9001, first.id) is False
    with pytest.raises(InvalidAction):
        await service.confirm(9001, second.id)
    with pytest.raises(AccessDenied):
        await service.confirm(101, first.id)
