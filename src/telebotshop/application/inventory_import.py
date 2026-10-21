import csv
from io import StringIO

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.inventory import (
    FINITE_TYPES,
    MAX_IMPORT_BYTES,
    MAX_IMPORT_ROWS,
    ImportFormat,
    ImportRow,
    InventoryImportError,
    payload_from_record,
)


def parse_inventory(
    content: bytes, format: ImportFormat, kind: ProductType
) -> tuple[ImportRow, ...]:
    if not isinstance(kind, ProductType) or kind not in FINITE_TYPES:
        raise InventoryImportError(None, "product_type")
    if not isinstance(format, ImportFormat):
        raise InventoryImportError(None, "format")
    if not isinstance(content, bytes) or len(content) > MAX_IMPORT_BYTES:
        raise InventoryImportError(None, "size")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InventoryImportError(None, "encoding") from None
    if not text:
        raise InventoryImportError(None, "empty")
    field = "key" if kind == ProductType.UNIQUE_KEY else "license_key"
    rows: list[ImportRow] = []

    def append(number: int, material: str) -> None:
        if len(rows) >= MAX_IMPORT_ROWS:
            raise InventoryImportError(number, "rows")
        try:
            payload = payload_from_record(kind, {field: material})
        except InvalidInput:
            raise InventoryImportError(number, "payload") from None
        rows.append(ImportRow(number, payload))

    if format == ImportFormat.TXT:
        lines = text.replace("\r\n", "\n").split("\n")
        if lines[-1] == "":
            lines.pop()
        for number, line in enumerate(lines, 1):
            append(number, line)
    else:
        reader = csv.reader(StringIO(text, newline=""), strict=True)
        number = 1
        try:
            if next(reader, None) != [field]:
                raise InventoryImportError(1, "header")
            while True:
                number = reader.line_num + 1
                record = next(reader, None)
                if record is None:
                    break
                if len(record) != 1:
                    raise InventoryImportError(number, "columns")
                append(number, record[0])
        except csv.Error:
            raise InventoryImportError(number, "csv") from None
    if not rows:
        raise InventoryImportError(None, "empty")
    return tuple(rows)
