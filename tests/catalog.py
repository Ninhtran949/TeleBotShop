from functools import partial

from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.models.catalog import (
    CategoryDraft,
    ProductDraft,
    ProductType,
    UniqueKeyMetadata,
)
from telebotshop.infrastructure.database.uow import unit_of_work


async def setup_catalog(database: AsyncEngine) -> CatalogService:
    factory = partial(unit_of_work, database)
    await IdentityService(factory).bootstrap_owner(9001)
    service = CatalogService(factory)
    root = await service.prepare_category(9001, CategoryDraft("demo", "Danh mục giả"))
    await service.confirm(9001, root.id)
    return service


def fake_product(sku: str = "DEMO-1", *, price: int = 100, category: str = "demo") -> ProductDraft:
    return ProductDraft(
        sku,
        category,
        ProductType.UNIQUE_KEY,
        "Sản phẩm giả",
        "Mô tả công khai",
        price,
        UniqueKeyMetadata(),
    )
