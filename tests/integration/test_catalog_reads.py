from dataclasses import asdict, replace

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.domain.models.catalog import (
    CategoryDraft,
    DigitalFileMetadata,
    LicenseMetadata,
    ManualMetadata,
    ProductType,
    ResourceFormat,
    ReusableMetadata,
    SaleState,
    UniqueKeyMetadata,
)
from tests.catalog import fake_product, setup_catalog


@pytest.mark.asyncio
async def test_all_five_types_round_trip_public_metadata_without_delivery_fields(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_catalog(migrated_database)
    fixtures = [
        (ProductType.UNIQUE_KEY, UniqueKeyMetadata()),
        (ProductType.LICENSE, LicenseMetadata("Hướng dẫn công khai")),
        (ProductType.DIGITAL_FILE, DigitalFileMetadata("demo.pdf", "application/pdf")),
        (ProductType.REUSABLE_RESOURCE, ReusableMetadata(ResourceFormat.TEXT)),
        (ProductType.MANUAL_FULFILLMENT, ManualMetadata("Nhân viên xử lý thủ công.")),
    ]
    for index, (kind, metadata) in enumerate(fixtures):
        draft = replace(fake_product(f"DEMO-{index}"), product_type=kind, metadata=metadata)
        action = await service.prepare_product(9001, draft)
        await service.confirm(9001, action.id)
        sale = await service.prepare_sale(9001, draft.sku, SaleState.ACTIVE)
        await service.confirm(9001, sale.id)
    page = await service.products(101)
    assert len(page.items) == 5 and not page.has_more
    assert {product.draft.product_type for product in page.items} == set(ProductType)
    for product in page.items:
        public = asdict(product)
        assert not set(public["draft"]["metadata"]) & {
            "key",
            "file_id",
            "content",
            "ciphertext",
            "staff_instructions",
        }


@pytest.mark.asyncio
async def test_search_pagination_and_parent_category_filter_query_real_active_rows(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_catalog(migrated_database)
    child = await service.prepare_category(9001, CategoryDraft("child", "Danh mục con", "demo"))
    await service.confirm(9001, child.id)
    for index in range(8):
        draft = replace(
            fake_product(f"SKU-{index}", category="child" if index % 2 else "demo"),
            name="Bản quyền phần mềm",
            description="100% _literal" if index == 0 else "Công khai",
        )
        action = await service.prepare_product(9001, draft)
        await service.confirm(9001, action.id)
        if index != 7:
            sale = await service.prepare_sale(9001, draft.sku, SaleState.ACTIVE)
            await service.confirm(9001, sale.id)
    first = await service.products(101, category_code="demo", query="BẢN QUYỀN")
    second = await service.products(101, category_code="demo", query="BẢN QUYỀN", page=1)
    assert len(first.items) == 5 and first.has_more
    assert len(second.items) == 2 and not second.has_more
    assert not {product.id for product in first.items} & {product.id for product in second.items}
    assert len((await service.products(101, category_code="child")).items) == 3
    for literal in ("%", "_"):
        matches = await service.products(101, query=literal)
        assert [product.draft.sku for product in matches.items] == ["SKU-0"]
    assert (await service.products(101, query="' OR 1=1 --")).items == ()
    assert (await service.products(101, page=9999)).items == ()
    assert len((await service.admin_products(9001, page=1)).items) == 3
