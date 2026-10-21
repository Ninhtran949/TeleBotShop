import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import (
    CategoryDraft,
    DigitalFileMetadata,
    LicenseMetadata,
    ManualMetadata,
    ProductDraft,
    ProductType,
    ResourceFormat,
    ReusableMetadata,
    UniqueKeyMetadata,
    metadata_from_record,
    validate_page,
    validate_search,
)


@pytest.mark.parametrize(
    ("kind", "metadata"),
    [
        (ProductType.UNIQUE_KEY, {}),
        (ProductType.LICENSE, {"instructions": "Hướng dẫn kích hoạt công khai"}),
        (ProductType.DIGITAL_FILE, {"file_name": "demo.pdf", "mime_type": "application/pdf"}),
        (ProductType.REUSABLE_RESOURCE, {"format": "TEXT"}),
        (ProductType.MANUAL_FULFILLMENT, {"customer_notice": "Nhân viên sẽ xử lý yêu cầu."}),
    ],
)
def test_five_types_have_distinct_validated_public_metadata(
    kind: ProductType, metadata: dict
) -> None:
    value = metadata_from_record(kind, metadata)
    product = ProductDraft("DEMO-1", "demo", kind, "Sản phẩm giả", "Mô tả", 100, value)
    assert product.product_type == kind


@pytest.mark.parametrize(
    ("kind", "metadata"),
    [
        (ProductType.UNIQUE_KEY, {"key": "FAKE_SECRET"}),
        (ProductType.LICENSE, {"file_name": "fake.txt", "mime_type": "text/plain"}),
        (
            ProductType.DIGITAL_FILE,
            {"file_name": "fake.txt", "mime_type": "text/plain", "file_id": "FAKE_SECRET"},
        ),
        (ProductType.REUSABLE_RESOURCE, {"format": "TEXT", "content": "FAKE_SECRET"}),
        (
            ProductType.MANUAL_FULFILLMENT,
            {"customer_notice": "", "staff_instructions": "FAKE_SECRET"},
        ),
    ],
)
def test_wrong_type_or_delivery_payload_is_rejected_without_echo(
    kind: ProductType, metadata: dict
) -> None:
    with pytest.raises(InvalidInput) as failure:
        metadata_from_record(kind, metadata)
    assert "FAKE_SECRET" not in str(failure.value)


@pytest.mark.parametrize("price", [True, 0, -1, 1.5, "100", 2**63])
def test_stars_price_is_positive_integer(price: object) -> None:
    with pytest.raises(InvalidInput):
        ProductDraft("SKU", "demo", ProductType.UNIQUE_KEY, "Fake", "", price, UniqueKeyMetadata())


def test_public_metadata_validation_covers_format_and_identity_boundaries() -> None:
    with pytest.raises(InvalidInput):
        DigitalFileMetadata("../private.txt", "text/plain")
    with pytest.raises(InvalidInput):
        LicenseMetadata(123)
    with pytest.raises(InvalidInput):
        ReusableMetadata("unknown")
    with pytest.raises(InvalidInput):
        ManualMetadata("\x00fake")
    assert ReusableMetadata(ResourceFormat.DOCUMENT).format == ResourceFormat.DOCUMENT
    with pytest.raises(InvalidInput):
        CategoryDraft("same", "Fake", "same")
    with pytest.raises(InvalidInput):
        ProductDraft("SKU", "demo", ProductType.LICENSE, "Fake", "", 100, UniqueKeyMetadata())


@pytest.mark.parametrize("page", [True, -1, 10_000, 1.5])
def test_pagination_bounds_are_checked_in_domain(page: object) -> None:
    with pytest.raises(InvalidInput):
        validate_page(page)


@pytest.mark.parametrize("query", ["", " " * 5, "a" * 81, "fake\x00query", 123])
def test_search_is_bounded_and_rejects_control_data(query: object) -> None:
    with pytest.raises(InvalidInput):
        validate_search(query)
