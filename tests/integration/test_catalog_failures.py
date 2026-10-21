import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.domain.errors import InvalidAction, InvalidInput
from telebotshop.domain.models.catalog import CategoryDraft
from tests.catalog import fake_product, setup_catalog


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_price_audit_or_commit_failure_rolls_back_price_version_and_action(
    migrated_database: AsyncEngine,
    deferred: bool,
) -> None:
    service = await setup_catalog(migrated_database)
    create = await service.prepare_product(9001, fake_product())
    await service.confirm(9001, create.id)
    action = await service.prepare_price(9001, "DEMO-1", 200)
    async with migrated_database.begin() as connection:
        await connection.execute(
            text("""CREATE FUNCTION fail_catalog_audit() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Synthetic catalog audit failure'
            USING ERRCODE = '23514'; END; $$""")
        )
        trigger = (
            "CREATE CONSTRAINT TRIGGER fail_catalog_audit AFTER INSERT ON audit_logs "
            "DEFERRABLE INITIALLY DEFERRED"
            if deferred
            else "CREATE TRIGGER fail_catalog_audit BEFORE INSERT ON audit_logs"
        )
        await connection.execute(
            text(
                trigger + " FOR EACH ROW WHEN (NEW.action = 'product.price') "
                "EXECUTE FUNCTION fail_catalog_audit()"
            )
        )
    with pytest.raises(DBAPIError):
        await service.confirm(9001, action.id)
    product = (await service.admin_products(9001)).items[0]
    assert product.draft.price_stars == 100 and product.version == 0
    async with migrated_database.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT status FROM catalog_actions WHERE id = :id"), {"id": action.id}
            )
            == "pending"
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action = 'product.price'")
            )
            == 0
        )


@pytest.mark.asyncio
async def test_duplicate_sku_and_category_creation_races_cannot_overwrite_or_double_audit(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_catalog(migrated_database)
    for drafts, prepare in (
        (
            (CategoryDraft("race", "Fake 1"), CategoryDraft("race", "Fake 2")),
            service.prepare_category,
        ),
        ((fake_product(price=100), fake_product(price=200)), service.prepare_product),
    ):
        first, second = await asyncio.gather(prepare(9001, drafts[0]), prepare(9001, drafts[1]))
        results = await asyncio.gather(
            service.confirm(9001, first.id),
            service.confirm(9001, second.id),
            return_exceptions=True,
        )
        assert sum(result is True for result in results) == 1
        assert sum(isinstance(result, InvalidAction) for result in results) == 1
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM products")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 4


@pytest.mark.asyncio
async def test_expired_cancelled_or_corrupted_actions_do_not_change_price(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_catalog(migrated_database)
    create = await service.prepare_product(9001, fake_product())
    await service.confirm(9001, create.id)
    expired = await service.prepare_price(9001, "DEMO-1", 200)
    async with migrated_database.begin() as connection:
        await connection.execute(
            text("UPDATE catalog_actions SET expires_at = now() - interval '1 second'")
        )
    with pytest.raises(InvalidAction):
        await service.confirm(9001, expired.id)
    cancelled = await service.prepare_price(9001, "DEMO-1", 300)
    await service.cancel(9001, cancelled.id)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, cancelled.id)
    invalid = await service.prepare_price(9001, "DEMO-1", 400)
    async with migrated_database.begin() as connection:
        await connection.execute(
            text("UPDATE catalog_actions SET payload = '{\"price_stars\": false}' WHERE id = :id"),
            {"id": invalid.id},
        )
    with pytest.raises(InvalidInput):
        await service.confirm(9001, invalid.id)
    assert (await service.admin_products(9001)).items[0].draft.price_stars == 100


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE products SET sku = 'CHANGED'",
        "UPDATE products SET product_type = 'LICENSE', "
        'public_metadata = \'{"instructions": "Public guide"}\'',
        "UPDATE products SET price_stars = 0",
        'UPDATE products SET public_metadata = \'{"key": "FAKE_SECRET"}\'',
        "UPDATE products SET public_metadata = '[]'",
        "UPDATE products SET image_file_id = 'https://fake.invalid/secret'",
        "UPDATE categories SET parent_id = id",
    ],
)
async def test_database_enforces_identity_price_metadata_and_image_boundaries(
    migrated_database: AsyncEngine,
    statement: str,
) -> None:
    service = await setup_catalog(migrated_database)
    create = await service.prepare_product(9001, fake_product())
    await service.confirm(9001, create.id)
    with pytest.raises(IntegrityError):
        async with migrated_database.begin() as connection:
            await connection.execute(text(statement))
    product = (await service.admin_products(9001)).items[0]
    assert product.draft.price_stars == 100 and product.draft.sku == "DEMO-1"
