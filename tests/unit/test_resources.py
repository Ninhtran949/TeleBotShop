from dataclasses import replace
from uuid import uuid4

import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.inventory import InventoryContext, InventoryCryptoError
from telebotshop.domain.models.resources import (
    DigitalFilePayload,
    ManualPayload,
    ResourceContext,
    ReusableDocumentPayload,
    ReusableTextPayload,
    resource_payload_from_record,
)
from tests.inventory import fake_cipher

FILE_ID = "FAKE_registered_document_12345"


@pytest.mark.parametrize(
    "kind,payload",
    [
        (ProductType.DIGITAL_FILE, DigitalFilePayload(FILE_ID, "demo.pdf", "application/pdf")),
        (ProductType.REUSABLE_RESOURCE, ReusableTextPayload("FAKE resource <&>\nline 2")),
        (ProductType.REUSABLE_RESOURCE, ReusableDocumentPayload(FILE_ID)),
        (
            ProductType.MANUAL_FULFILLMENT,
            ManualPayload("Đang chờ xử lý.", "FAKE private instructions"),
        ),
    ],
)
def test_resource_encryption_roundtrip_is_bound_to_product_version_sku_and_type(kind, payload):
    cipher = fake_cipher()
    context = ResourceContext(uuid4(), "DEMO-1", uuid4(), kind)
    sealed = cipher.seal(context, payload)
    assert cipher.open(context, sealed) == payload
    assert b"FAKE" not in sealed.ciphertext
    assert "FAKE" not in repr(payload) + repr(context) + repr(sealed)
    for changed in (
        replace(context, product_id=uuid4()),
        replace(context, resource_id=uuid4()),
        replace(context, sku="OTHER"),
        replace(
            context,
            kind=ProductType.MANUAL_FULFILLMENT
            if kind != ProductType.MANUAL_FULFILLMENT
            else ProductType.DIGITAL_FILE,
        ),
    ):
        with pytest.raises(InventoryCryptoError):
            cipher.open(changed, sealed)


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.org/file",
        "file:///tmp/a",
        "../file",
        "attach://a",
        "x",
        "a" * 513,
        None,
        123,
    ],
)
def test_document_payload_rejects_urls_paths_and_unbounded_references(reference):
    for constructor in (
        lambda value: DigitalFilePayload(value, "demo.pdf", "application/pdf"),
        ReusableDocumentPayload,
    ):
        with pytest.raises(InvalidInput):
            constructor(reference)


@pytest.mark.parametrize(
    "record",
    [
        {"key": "FAKE"},
        {"file_id": FILE_ID, "text": "FAKE"},
        {"text": ""},
        {"text": "x" * 4097},
        {"text": "\ud800"},
        {"text": "FAKE\x00"},
        None,
    ],
)
def test_resource_decoder_rejects_wrong_shape_and_invalid_material(record):
    with pytest.raises(InvalidInput):
        resource_payload_from_record(ProductType.REUSABLE_RESOURCE, record)


def test_resource_crypto_never_casts_wrong_type_and_keeps_finite_namespace_separate():
    cipher = fake_cipher()
    context = ResourceContext(uuid4(), "DEMO-1", uuid4(), ProductType.DIGITAL_FILE)
    with pytest.raises(InventoryCryptoError):
        cipher.seal(context, ReusableTextPayload("FAKE"))
    with pytest.raises(InventoryCryptoError):
        cipher.seal(context, ReusableDocumentPayload(FILE_ID))
    with pytest.raises(InventoryCryptoError):
        InventoryContext(context.product_id, context.sku, context.resource_id, context.kind)
    with pytest.raises(InventoryCryptoError):
        ResourceContext(context.product_id, context.sku, context.resource_id, ProductType.LICENSE)


def test_manual_payload_needs_distinct_customer_notice_and_private_instructions():
    with pytest.raises(InvalidInput):
        ManualPayload("", "FAKE private")
    with pytest.raises(InvalidInput):
        ManualPayload("Đang chờ.", "")
    with pytest.raises(InvalidInput):
        ManualPayload("Đang chờ.", "x" * 2049)
