import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput

PAGE_SIZE = 5


class ProductType(StrEnum):
    UNIQUE_KEY = "UNIQUE_KEY"
    LICENSE = "LICENSE"
    DIGITAL_FILE = "DIGITAL_FILE"
    REUSABLE_RESOURCE = "REUSABLE_RESOURCE"
    MANUAL_FULFILLMENT = "MANUAL_FULFILLMENT"


class SaleState(StrEnum):
    HIDDEN = "HIDDEN"
    ACTIVE = "ACTIVE"


class ResourceFormat(StrEnum):
    TEXT = "TEXT"
    DOCUMENT = "DOCUMENT"


def validate_text(value: str, maximum: int, *, empty: bool = False) -> None:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or not empty
        and not value.strip()
        or any(ord(character) < 32 and character not in "\n\t" for character in value)
    ):
        raise InvalidInput("Invalid catalog text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidInput("Invalid catalog text") from None


def validate_code(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", value):
        raise InvalidInput("Invalid catalog code")


def validate_price(value: int) -> None:
    if type(value) is not int or not 0 < value < 2**63:
        raise InvalidInput("Invalid Stars price")


def validate_page(page: int) -> None:
    if type(page) is not int or not 0 <= page < 10_000:
        raise InvalidInput("Invalid catalog page")


def validate_search(query: str) -> None:
    validate_text(query, 80)
    if "\n" in query or "\t" in query:
        raise InvalidInput("Invalid search")


def validate_image_id(image_id: str | None) -> None:
    if image_id is not None and (
        not isinstance(image_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", image_id)
    ):
        raise InvalidInput("Invalid public photo reference")


@dataclass(frozen=True, repr=False)
class UniqueKeyMetadata:
    pass


@dataclass(frozen=True, repr=False)
class LicenseMetadata:
    instructions: str

    def __post_init__(self) -> None:
        validate_text(self.instructions, 500)


@dataclass(frozen=True, repr=False)
class DigitalFileMetadata:
    file_name: str
    mime_type: str

    def __post_init__(self) -> None:
        validate_text(self.file_name, 128)
        if (
            "/" in self.file_name
            or "\\" in self.file_name
            or self.file_name in {".", ".."}
            or not isinstance(self.mime_type, str)
            or not re.fullmatch(
                r"[A-Za-z0-9!#$&^_.+-]{1,64}/[A-Za-z0-9!#$&^_.+-]{1,64}", self.mime_type
            )
        ):
            raise InvalidInput("Invalid public file metadata")


@dataclass(frozen=True, repr=False)
class ReusableMetadata:
    format: ResourceFormat

    def __post_init__(self) -> None:
        if not isinstance(self.format, ResourceFormat):
            raise InvalidInput("Invalid resource format")


@dataclass(frozen=True, repr=False)
class ManualMetadata:
    customer_notice: str

    def __post_init__(self) -> None:
        validate_text(self.customer_notice, 500)


type ProductMetadata = (
    UniqueKeyMetadata | LicenseMetadata | DigitalFileMetadata | ReusableMetadata | ManualMetadata
)

METADATA_TYPES = {
    ProductType.UNIQUE_KEY: UniqueKeyMetadata,
    ProductType.LICENSE: LicenseMetadata,
    ProductType.DIGITAL_FILE: DigitalFileMetadata,
    ProductType.REUSABLE_RESOURCE: ReusableMetadata,
    ProductType.MANUAL_FULFILLMENT: ManualMetadata,
}


def metadata_from_record(product_type: ProductType, raw: object) -> ProductMetadata:
    if not isinstance(product_type, ProductType) or not isinstance(raw, dict):
        raise InvalidInput("Invalid product metadata")
    data = dict(raw)
    try:
        if product_type == ProductType.REUSABLE_RESOURCE and "format" in data:
            data["format"] = ResourceFormat(data["format"])
        return METADATA_TYPES[product_type](**data)
    except (TypeError, ValueError):
        raise InvalidInput("Invalid product metadata") from None


@dataclass(frozen=True, repr=False)
class CategoryDraft:
    code: str
    name: str
    parent_code: str | None = None

    def __post_init__(self) -> None:
        validate_code(self.code)
        validate_text(self.name, 60)
        if self.parent_code is not None:
            validate_code(self.parent_code)
            if self.parent_code == self.code:
                raise InvalidInput("Invalid parent category")


@dataclass(frozen=True, repr=False)
class Category:
    id: UUID
    draft: CategoryDraft


@dataclass(frozen=True, repr=False)
class ProductDraft:
    sku: str
    category_code: str
    product_type: ProductType
    name: str
    description: str
    price_stars: int
    metadata: ProductMetadata

    def __post_init__(self) -> None:
        validate_code(self.sku)
        validate_code(self.category_code)
        validate_text(self.name, 80)
        validate_text(self.description, 1200, empty=True)
        validate_price(self.price_stars)
        if (
            not isinstance(self.product_type, ProductType)
            or type(self.metadata) is not METADATA_TYPES[self.product_type]
        ):
            raise InvalidInput("Invalid product metadata type")


@dataclass(frozen=True, repr=False)
class Product:
    id: UUID
    draft: ProductDraft
    sale_state: SaleState
    version: int
    image_file_id: str | None = None


@dataclass(frozen=True)
class Availability:
    available: int | None
    ready: bool


@dataclass(frozen=True, repr=False)
class Page[T]:
    items: tuple[T, ...]
    page: int
    has_more: bool
