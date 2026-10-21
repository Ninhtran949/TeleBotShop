import pytest

from telebotshop.admin.catalog import parse_product_form
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import ProductType


def test_product_form_keeps_an_explicit_empty_description() -> None:
    draft = parse_product_form("SKU | demo | UNIQUE_KEY | 100\nTên giả\n")
    assert draft.description == "" and draft.product_type == ProductType.UNIQUE_KEY


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        (ProductType.LICENSE, "Hướng dẫn kích hoạt công khai"),
        (ProductType.DIGITAL_FILE, "demo.pdf | application/pdf"),
        (ProductType.REUSABLE_RESOURCE, "DOCUMENT"),
        (ProductType.MANUAL_FULFILLMENT, "Nhân viên xử lý thủ công."),
    ],
)
def test_other_types_parse_their_own_public_fields(kind: ProductType, extra: str) -> None:
    draft = parse_product_form(f"SKU | demo | {kind} | 100\nTên giả\nCông khai\n{extra}\n")
    assert draft.product_type == kind


@pytest.mark.parametrize(
    "form",
    [
        "SKU | demo | UNIQUE_KEY | 100\nName\nPublic\nFAKE_SECRET",
        "SKU | demo | DIGITAL_FILE | 100\nName\nPublic\nfile.pdf | application/pdf | FAKE_SECRET",
        "SKU | demo | UNIQUE_KEY | 1.5\nName\nPublic",
        "SKU | demo | UNIQUE_KEY | -1\nName\nPublic",
        "SKU | demo | LICENSE | 100\nName\nPublic",
    ],
)
def test_form_rejects_invalid_price_and_wrong_type_fields_without_echo(form: str) -> None:
    with pytest.raises(InvalidInput) as failure:
        parse_product_form(form)
    assert "FAKE_SECRET" not in str(failure.value)
