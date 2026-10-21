from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import (
    CategoryDraft,
    ProductDraft,
    ProductType,
    SaleState,
    metadata_from_record,
    validate_image_id,
    validate_price,
)


class CatalogActionKind(StrEnum):
    CATEGORY = "category.create"
    CREATE = "product.create"
    EDIT = "product.edit"
    PRICE = "product.price"
    SALE = "product.sale"
    IMAGE = "product.image"


@dataclass(frozen=True, repr=False)
class PriceChange:
    price_stars: int

    def __post_init__(self) -> None:
        validate_price(self.price_stars)


@dataclass(frozen=True, repr=False)
class SaleChange:
    sale_state: SaleState

    def __post_init__(self) -> None:
        if not isinstance(self.sale_state, SaleState):
            raise InvalidInput("Invalid sale state")


@dataclass(frozen=True, repr=False)
class ImageChange:
    image_file_id: str | None

    def __post_init__(self) -> None:
        validate_image_id(self.image_file_id)


type CatalogPayload = CategoryDraft | ProductDraft | PriceChange | SaleChange | ImageChange


def payload_from_record(kind: CatalogActionKind, raw: object) -> CatalogPayload:
    if not isinstance(raw, dict):
        raise InvalidInput("Invalid catalog action")
    data = dict(raw)
    try:
        match kind:
            case CatalogActionKind.CATEGORY:
                return CategoryDraft(**data)
            case CatalogActionKind.CREATE | CatalogActionKind.EDIT:
                data["product_type"] = ProductType(data["product_type"])
                data["metadata"] = metadata_from_record(data["product_type"], data["metadata"])
                return ProductDraft(**data)
            case CatalogActionKind.PRICE:
                return PriceChange(**data)
            case CatalogActionKind.SALE:
                data["sale_state"] = SaleState(data["sale_state"])
                return SaleChange(**data)
            case CatalogActionKind.IMAGE:
                return ImageChange(**data)
    except (KeyError, TypeError, ValueError):
        raise InvalidInput("Invalid catalog action") from None
    raise InvalidInput("Invalid catalog action")


@dataclass(frozen=True, repr=False)
class CatalogAction:
    id: UUID
    actor_id: UUID
    kind: CatalogActionKind
    target_id: UUID
    payload: CatalogPayload
    expected_version: int
    expires_at: datetime
    status: ActionStatus = ActionStatus.PENDING
