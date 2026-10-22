from dataclasses import replace
from functools import partial

from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.resources import ResourceService
from telebotshop.domain.models.catalog import (
    DigitalFileMetadata,
    ManualMetadata,
    ProductType,
    ResourceFormat,
    ReusableMetadata,
    SaleState,
)
from telebotshop.domain.models.identity import CustomerIdentity
from telebotshop.domain.models.resources import (
    DigitalFilePayload,
    ManualPayload,
    ReusableDocumentPayload,
    ReusableTextPayload,
)
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.catalog import fake_product, setup_catalog
from tests.inventory import fake_cipher

FILE_ID = "FAKE_registered_document_12345"


def resource_service(engine):
    return ResourceService(partial(unit_of_work, engine), fake_cipher())


def resource_payload(kind, *, document=False):
    if kind == ProductType.DIGITAL_FILE:
        return DigitalFilePayload(FILE_ID, "demo.pdf", "application/pdf")
    if kind == ProductType.REUSABLE_RESOURCE:
        return (
            ReusableDocumentPayload(FILE_ID)
            if document
            else ReusableTextPayload("FAKE reusable <&>\ncontent")
        )
    return ManualPayload("Đơn đang chờ xử lý thủ công.", "FAKE private staff instructions")


async def resource_product(engine, kind=ProductType.DIGITAL_FILE, *, document=False, publish=True):
    catalog = await setup_catalog(engine)
    metadata = (
        DigitalFileMetadata("demo.pdf", "application/pdf")
        if kind == ProductType.DIGITAL_FILE
        else ReusableMetadata(ResourceFormat.DOCUMENT if document else ResourceFormat.TEXT)
        if kind == ProductType.REUSABLE_RESOURCE
        else ManualMetadata("Đơn đang chờ xử lý thủ công.")
    )
    draft = replace(fake_product(), product_type=kind, metadata=metadata)
    action = await catalog.prepare_product(9001, draft)
    await catalog.confirm(9001, action.id)
    action = await catalog.prepare_sale(9001, draft.sku, SaleState.ACTIVE)
    await catalog.confirm(9001, action.id)
    product = await catalog.admin_product(9001, draft.sku)
    identity = IdentityService(partial(unit_of_work, engine))
    for buyer in (101, 102):
        await identity.get_or_create_customer(CustomerIdentity(buyer, None, "Fake buyer"))
    version = None
    if publish:
        action = await resource_service(engine).prepare(
            9001, draft.sku, resource_payload(kind, document=document)
        )
        await resource_service(engine).confirm(9001, action.id)
        version = action
    return product, version
