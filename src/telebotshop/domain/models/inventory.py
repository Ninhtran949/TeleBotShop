import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import Page, ProductType, validate_code

MAX_IMPORT_BYTES = 1024 * 1024
MAX_IMPORT_ROWS = 1000
MAX_KEY_BYTES = 4096
FINITE_TYPES = frozenset({ProductType.UNIQUE_KEY, ProductType.LICENSE})


class ImportFormat(StrEnum):
    TXT = "txt"
    CSV = "csv"


class InventoryImportError(InvalidInput):
    def __init__(self, row: int | None, code: str) -> None:
        self.row, self.code = row, code
        super().__init__(f"Inventory import row={row} code={code}")


def validate_material(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise InvalidInput("Invalid inventory payload")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidInput("Invalid inventory payload") from None
    if len(encoded) > MAX_KEY_BYTES:
        raise InvalidInput("Invalid inventory payload")


@dataclass(frozen=True, repr=False)
class UniqueKeyPayload:
    key: str

    def __post_init__(self) -> None:
        validate_material(self.key)


@dataclass(frozen=True, repr=False)
class LicensePayload:
    license_key: str

    def __post_init__(self) -> None:
        validate_material(self.license_key)


type SecretPayload = UniqueKeyPayload | LicensePayload


def payload_from_record(kind: ProductType, record: object) -> SecretPayload:
    if isinstance(record, dict):
        if kind == ProductType.UNIQUE_KEY and set(record) == {"key"}:
            return UniqueKeyPayload(record["key"])
        if kind == ProductType.LICENSE and set(record) == {"license_key"}:
            return LicensePayload(record["license_key"])
    raise InvalidInput("Invalid inventory payload type")


@dataclass(frozen=True, repr=False)
class ImportRow:
    row: int
    payload: SecretPayload


class InventoryCryptoError(Exception):
    pass


@dataclass(frozen=True, repr=False)
class InventoryContext:
    product_id: UUID
    sku: str
    unit_id: UUID
    kind: ProductType

    def __post_init__(self) -> None:
        validate_code(self.sku)
        if (
            not isinstance(self.product_id, UUID)
            or not isinstance(self.unit_id, UUID)
            or not isinstance(self.kind, ProductType)
            or self.kind not in FINITE_TYPES
        ):
            raise InventoryCryptoError("Invalid inventory context")


@dataclass(frozen=True, repr=False)
class SealedPayload:
    key_version: int
    nonce: bytes
    ciphertext: bytes

    def __post_init__(self) -> None:
        if (
            type(self.key_version) is not int
            or not 0 < self.key_version < 2**31
            or not isinstance(self.nonce, bytes)
            or len(self.nonce) != 12
            or not isinstance(self.ciphertext, bytes)
            or not 17 <= len(self.ciphertext) <= MAX_KEY_BYTES * 2 + 128
        ):
            raise InventoryCryptoError("Invalid encrypted envelope")


@dataclass(frozen=True, repr=False)
class CryptoProof:
    dedup: bytes
    keys: tuple[tuple[int, bytes], ...]


@dataclass(frozen=True, repr=False)
class SealedUnit:
    id: UUID
    source_row: int
    fingerprint: bytes
    sealed: SealedPayload

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, UUID)
            or type(self.source_row) is not int
            or not 1 <= self.source_row <= MAX_IMPORT_ROWS + 1
            or not isinstance(self.fingerprint, bytes)
            or len(self.fingerprint) != 32
            or not isinstance(self.sealed, SealedPayload)
        ):
            raise InventoryCryptoError("Invalid encrypted unit")


class InventoryState(StrEnum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    RETIRED = "RETIRED"


class InventoryActionKind(StrEnum):
    IMPORT = "inventory.import"
    RETIRE = "inventory.retire"


@dataclass(frozen=True, repr=False)
class InventoryAction:
    id: UUID
    actor_id: UUID
    kind: InventoryActionKind
    product_id: UUID
    unit_id: UUID | None
    quantity: int
    expires_at: datetime
    status: ActionStatus = ActionStatus.PENDING


@dataclass(frozen=True, repr=False)
class InventoryUnitView:
    id: UUID
    product_id: UUID
    status: InventoryState


@dataclass(frozen=True, repr=False)
class StockSummary:
    product_id: UUID
    sku: str
    available: int
    reserved: int
    retired: int
    low_stock: bool


@dataclass(frozen=True, repr=False)
class StockPage:
    summary: StockSummary
    units: Page[InventoryUnitView]
