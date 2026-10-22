import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import (
    DigitalFileMetadata,
    ManualMetadata,
    ProductMetadata,
    ProductType,
    ResourceFormat,
    ReusableMetadata,
    validate_code,
    validate_text,
)
from telebotshop.domain.models.inventory import InventoryCryptoError

RESOURCE_TYPES = frozenset(ProductType) - {ProductType.UNIQUE_KEY, ProductType.LICENSE}


def validate_file_id(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", value):
        raise InvalidInput("Invalid registered document reference")


def validate_resource_text(value: str, maximum_bytes: int) -> None:
    validate_text(value, maximum_bytes)
    if len(value.encode("utf-8")) > maximum_bytes:
        raise InvalidInput("Invalid resource content")


@dataclass(frozen=True, repr=False)
class DigitalFilePayload:
    file_id: str
    file_name: str
    mime_type: str

    def __post_init__(self) -> None:
        validate_file_id(self.file_id)
        DigitalFileMetadata(self.file_name, self.mime_type)


@dataclass(frozen=True, repr=False)
class ReusableTextPayload:
    text: str

    def __post_init__(self) -> None:
        validate_resource_text(self.text, 4096)


@dataclass(frozen=True, repr=False)
class ReusableDocumentPayload:
    file_id: str

    def __post_init__(self) -> None:
        validate_file_id(self.file_id)


@dataclass(frozen=True, repr=False)
class ManualPayload:
    customer_notice: str
    staff_instructions: str

    def __post_init__(self) -> None:
        validate_text(self.customer_notice, 500)
        validate_resource_text(self.staff_instructions, 2048)


type ResourcePayload = (
    DigitalFilePayload | ReusableTextPayload | ReusableDocumentPayload | ManualPayload
)


def resource_payload_from_record(kind: ProductType, record: object) -> ResourcePayload:
    if isinstance(kind, ProductType) and isinstance(record, dict):
        if kind == ProductType.DIGITAL_FILE and set(record) == {
            "file_id",
            "file_name",
            "mime_type",
        }:
            return DigitalFilePayload(record["file_id"], record["file_name"], record["mime_type"])
        if kind == ProductType.REUSABLE_RESOURCE:
            if set(record) == {"text"}:
                return ReusableTextPayload(record["text"])
            if set(record) == {"file_id"}:
                return ReusableDocumentPayload(record["file_id"])
        if kind == ProductType.MANUAL_FULFILLMENT and set(record) == {
            "customer_notice",
            "staff_instructions",
        }:
            return ManualPayload(record["customer_notice"], record["staff_instructions"])
    raise InvalidInput("Invalid resource payload type")


@dataclass(frozen=True, repr=False)
class ResourceContext:
    product_id: UUID
    sku: str
    resource_id: UUID
    kind: ProductType

    def __post_init__(self) -> None:
        validate_code(self.sku)
        if (
            not isinstance(self.product_id, UUID)
            or not isinstance(self.resource_id, UUID)
            or not isinstance(self.kind, ProductType)
            or self.kind not in RESOURCE_TYPES
        ):
            raise InventoryCryptoError("Invalid resource context")


class ResourceState(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    CANCELLED = "CANCELLED"


def validate_resource_payload(
    kind: ProductType, metadata: ProductMetadata, payload: ResourcePayload
) -> None:
    from dataclasses import asdict

    if not isinstance(
        payload, (DigitalFilePayload, ReusableTextPayload, ReusableDocumentPayload, ManualPayload)
    ):
        raise InvalidInput("Invalid resource payload")
    decoded = resource_payload_from_record(kind, asdict(payload))
    if type(decoded) is not type(payload):
        raise InvalidInput("Invalid resource payload type")
    if isinstance(metadata, DigitalFileMetadata) and (
        not isinstance(payload, DigitalFilePayload)
        or (payload.file_name, payload.mime_type) != (metadata.file_name, metadata.mime_type)
    ):
        raise InvalidInput("Document metadata mismatch")
    if isinstance(metadata, ReusableMetadata) and (
        metadata.format == ResourceFormat.TEXT
        and not isinstance(payload, ReusableTextPayload)
        or metadata.format == ResourceFormat.DOCUMENT
        and not isinstance(payload, ReusableDocumentPayload)
    ):
        raise InvalidInput("Resource format mismatch")
    if isinstance(metadata, ManualMetadata) and (
        not isinstance(payload, ManualPayload)
        or payload.customer_notice != metadata.customer_notice
    ):
        raise InvalidInput("Manual notice mismatch")


@dataclass(frozen=True, repr=False)
class ResourceVersion:
    id: UUID
    product_id: UUID
    sku: str
    kind: ProductType
    metadata: ProductMetadata
    actor_id: UUID
    product_version: int
    expected_resource_id: UUID | None
    expires_at: datetime
    state: ResourceState = ResourceState.PENDING

    @property
    def context(self) -> ResourceContext:
        return ResourceContext(self.product_id, self.sku, self.id, self.kind)
