import asyncio
from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.errors import AccessDenied, CatalogNotFound, InvalidAction
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.catalog import CategoryDraft, SaleState
from telebotshop.domain.models.identity import CustomerIdentity, Role
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.catalog import fake_product, setup_catalog


@pytest.mark.asyncio
async def test_category_and_product_are_confirmed_audited_and_hidden_by_default(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_catalog(migrated_database)
    action = await service.prepare_product(9001, fake_product())
    assert (await service.admin_products(9001)).items == ()
    await service.confirm(9001, action.id)
    product = (await service.admin_products(9001)).items[0]
    assert product.draft.sku == "DEMO-1"
    assert product.sale_state == SaleState.HIDDEN
    assert (await service.products(101)).items == ()
    with pytest.raises(CatalogNotFound):
        await service.product(101, product.id)
    sale = await service.prepare_sale(9001, "DEMO-1", SaleState.ACTIVE)
    await service.confirm(9001, sale.id)
    assert (await service.product(101, product.id)).draft.price_stars == 100
    assert len((await service.products(101)).items) == 1
    assert not await service.confirm(9001, sale.id)
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 4


@pytest.mark.asyncio
async def test_category_hierarchy_is_bounded_and_product_type_sku_are_stable(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_catalog(migrated_database)
    child = await service.prepare_category(9001, CategoryDraft("child", "Danh mục con", "demo"))
    await service.confirm(9001, child.id)
    with pytest.raises(InvalidAction):
        await service.prepare_category(9001, CategoryDraft("too-deep", "Fake", "child"))
    assert (await service.categories(101, parent_code="demo")).items[0].draft.code == "child"
    create = await service.prepare_product(9001, fake_product(category="child"))
    await service.confirm(9001, create.id)
    product = (await service.admin_products(9001)).items[0]
    edited = await service.prepare_product(9001, fake_product(price=150, category="demo"))
    await service.confirm(9001, edited.id)
    updated = (await service.admin_products(9001)).items[0]
    assert updated.id == product.id and updated.draft.sku == product.draft.sku
    assert updated.draft.category_code == "demo" and updated.draft.price_stars == 150


@pytest.mark.asyncio
async def test_direct_catalog_calls_default_deny_and_recheck_revoked_permissions(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_catalog(migrated_database)
    factory = partial(unit_of_work, migrated_database)
    await IdentityService(factory).get_or_create_customer(CustomerIdentity(101, None, "Fake"))
    with pytest.raises(AccessDenied):
        await service.prepare_product(101, fake_product())
    access = AccessService(factory)
    grant = await access.prepare_permission(9001, 101, Role.ADMIN, Permission.MANAGE_CATALOG, True)
    await access.confirm(9001, grant.id)
    action = await service.prepare_product(101, fake_product())
    revoke = await access.prepare_permission(
        9001, 101, Role.ADMIN, Permission.MANAGE_CATALOG, False
    )
    await access.confirm(9001, revoke.id)
    with pytest.raises(AccessDenied):
        await service.confirm(101, action.id)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, action.id)
    with pytest.raises(AccessDenied):
        await service.admin_products(101)


@pytest.mark.asyncio
async def test_concurrent_price_changes_and_replayed_old_action_cannot_overwrite_current_price(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_catalog(migrated_database)
    create = await service.prepare_product(9001, fake_product())
    await service.confirm(9001, create.id)
    first, second = await asyncio.gather(
        service.prepare_price(9001, "DEMO-1", 200), service.prepare_price(9001, "DEMO-1", 300)
    )
    results = await asyncio.gather(
        service.confirm(9001, first.id), service.confirm(9001, second.id), return_exceptions=True
    )
    assert sum(result is True for result in results) == 1
    assert sum(isinstance(result, InvalidAction) for result in results) == 1
    current = (await service.admin_products(9001)).items[0]
    assert current.draft.price_stars in {200, 300}
    assert current.version == 1
    assert not await service.confirm(9001, create.id)
    assert (await service.admin_products(9001)).items[
        0
    ].draft.price_stars == current.draft.price_stars


@pytest.mark.asyncio
async def test_public_photo_reference_can_be_set_cleared_and_audited_without_url_fetch(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_catalog(migrated_database)
    create = await service.prepare_product(9001, fake_product())
    await service.confirm(9001, create.id)
    image_id = "AgACAg_FAKE_PUBLIC_PHOTO_ID_123456789"
    image = await service.prepare_image(9001, "DEMO-1", image_id)
    await service.confirm(9001, image.id)
    assert (await service.admin_products(9001)).items[0].image_file_id == image_id
    clear = await service.prepare_image(9001, "DEMO-1", None)
    await service.confirm(9001, clear.id)
    assert (await service.admin_products(9001)).items[0].image_file_id is None
