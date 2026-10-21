import pytest

from telebotshop.application.inventory_import import parse_inventory
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.inventory import (
    MAX_IMPORT_BYTES,
    ImportFormat,
    InventoryImportError,
    LicensePayload,
    UniqueKeyPayload,
)


def test_txt_preserves_case_spaces_and_unicode_material_without_leaking_repr() -> None:
    material = [" Fake-AbC ", "fake-abc", "é", "e\u0301"]
    rows = parse_inventory(
        ("\r\n".join(material) + "\r\n").encode(), ImportFormat.TXT, ProductType.UNIQUE_KEY
    )
    assert [row.payload.key for row in rows] == material
    assert [row.row for row in rows] == [1, 2, 3, 4]
    assert "Fake-AbC" not in repr(rows)


def test_csv_license_payload_is_distinct_and_accepts_quoted_commas() -> None:
    rows = parse_inventory(
        b'\xef\xbb\xbflicense_key\r\n"FAKE,license"\r\n', ImportFormat.CSV, ProductType.LICENSE
    )
    assert rows[0].row == 2
    assert isinstance(rows[0].payload, LicensePayload)
    assert rows[0].payload.license_key == "FAKE,license"


@pytest.mark.parametrize(
    ("content", "format", "kind", "row", "code"),
    [
        (b"", ImportFormat.TXT, ProductType.UNIQUE_KEY, None, "empty"),
        (b"fake\n\nother", ImportFormat.TXT, ProductType.UNIQUE_KEY, 2, "payload"),
        (b"FAKE\x00secret", ImportFormat.TXT, ProductType.UNIQUE_KEY, 1, "payload"),
        (b"\xffFAKE", ImportFormat.TXT, ProductType.UNIQUE_KEY, None, "encoding"),
        (b"key\nFAKE", ImportFormat.CSV, ProductType.LICENSE, 1, "header"),
        (b"key\nFAKE,secret", ImportFormat.CSV, ProductType.UNIQUE_KEY, 2, "columns"),
        (b'key\n"FAKE\nsecret"', ImportFormat.CSV, ProductType.UNIQUE_KEY, 2, "payload"),
        (b'key\n"FAKE', ImportFormat.CSV, ProductType.UNIQUE_KEY, 2, "csv"),
        (b"FAKE", ImportFormat.TXT, ProductType.DIGITAL_FILE, None, "product_type"),
        (b"FAKE", "txt", ProductType.UNIQUE_KEY, None, "format"),
    ],
)
def test_invalid_batch_reports_only_row_and_code(content, format, kind, row, code) -> None:
    with pytest.raises(InventoryImportError) as captured:
        parse_inventory(content, format, kind)
    assert (captured.value.row, captured.value.code) == (row, code)
    assert "FAKE" not in str(captured.value) and "secret" not in repr(captured.value)


def test_import_byte_and_row_limits_apply_before_any_storage() -> None:
    with pytest.raises(InventoryImportError, match="size"):
        parse_inventory(b"x" * (MAX_IMPORT_BYTES + 1), ImportFormat.TXT, ProductType.UNIQUE_KEY)
    assert len(parse_inventory(b"x\n" * 1000, ImportFormat.TXT, ProductType.UNIQUE_KEY)) == 1000
    with pytest.raises(InventoryImportError, match="rows"):
        parse_inventory(b"x\n" * 1001, ImportFormat.TXT, ProductType.UNIQUE_KEY)


@pytest.mark.parametrize("material", ["", " \t", "x" * 4097, "\ud800", "x\x7f"])
def test_secret_payload_validation_is_shared_by_direct_callers(material: str) -> None:
    for payload in (UniqueKeyPayload, LicensePayload):
        with pytest.raises(InvalidInput):
            payload(material)
