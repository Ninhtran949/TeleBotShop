from functools import partial
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.domain.models.access import ROLE_PERMISSIONS
from telebotshop.infrastructure.database.models.identity import metadata
from telebotshop.infrastructure.database.schema import SCHEMA_HEAD
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.inventory import setup_inventory


@pytest.mark.asyncio
async def test_t04_upgrade_preserves_catalog_audit_and_pending_product_confirmation(database):
    async with database.begin() as connection:
        await connection.run_sync(upgrade, "0004_catalog")
    product = await setup_inventory(database)
    catalog = CatalogService(partial(unit_of_work, database))
    pending = await catalog.prepare_price(9001, "DEMO-1", 300)
    async with database.begin() as connection:
        audit_count = await connection.scalar(text("SELECT count(*) FROM audit_logs"))
        await connection.run_sync(upgrade)
        assert await connection.scalar(text("SELECT id FROM products")) == product.id
        assert await connection.scalar(text("SELECT price_stars FROM products")) == 100
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == audit_count
        assert await connection.scalar(text("SELECT count(*) FROM inventory_units")) == 0
    assert await catalog.confirm(9001, pending.id) is True
    assert (await catalog.admin_product(9001, "DEMO-1")).draft.price_stars == 300


def upgrade(connection: Connection, revision: str = "head") -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


@pytest.mark.asyncio
async def test_clean_schema_upgrades_to_baseline(database: AsyncEngine) -> None:
    async with database.begin() as connection:
        assert await connection.scalar(text("SELECT to_regclass('alembic_version')")) is None
        await connection.run_sync(upgrade)
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_HEAD
        )


@pytest.mark.asyncio
async def test_upgrade_from_base_is_repeatable_and_preserves_existing_rows(
    database: AsyncEngine,
) -> None:
    async with database.begin() as connection:
        await connection.run_sync(upgrade, "base")
        await connection.execute(text("CREATE TABLE migration_probe (value integer NOT NULL)"))
        await connection.execute(text("INSERT INTO migration_probe VALUES (42)"))
        await connection.run_sync(upgrade)
        await connection.run_sync(upgrade)
        assert await connection.scalar(text("SELECT value FROM migration_probe")) == 42
        assert (
            await connection.scalars(text("SELECT version_num FROM alembic_version"))
        ).all() == [SCHEMA_HEAD]


@pytest.mark.asyncio
async def test_t02_upgrade_and_identity_upgrade_preserve_profiles_and_owner(
    database: AsyncEngine,
) -> None:
    async with database.begin() as connection:
        await connection.run_sync(upgrade, "0001_runtime")
        await connection.run_sync(upgrade, "0002_identity")
        await connection.execute(text("INSERT INTO users (telegram_id) VALUES (9001)"))
        user_id = await connection.scalar(text("SELECT id FROM users"))
        await connection.execute(text("INSERT INTO user_roles SELECT id, 'OWNER' FROM users"))
        await connection.execute(
            text("UPDATE shop_settings SET owner_user_id = :id"), {"id": user_id}
        )
        registered = await connection.scalar(text("SELECT registered_at FROM users"))
        await connection.run_sync(upgrade)
        assert await connection.scalar(text("SELECT id FROM users")) == user_id
        assert await connection.scalar(text("SELECT registered_at FROM users")) == registered
        assert await connection.scalar(text("SELECT owner_user_id FROM shop_settings")) == user_id
        assert await connection.scalar(text("SELECT role FROM user_roles")) == "OWNER"


@pytest.mark.asyncio
async def test_migration_matches_metadata_and_role_permission_contract(
    migrated_database: AsyncEngine,
) -> None:
    async with migrated_database.connect() as connection:
        differences = await connection.run_sync(
            lambda sync: compare_metadata(
                MigrationContext.configure(sync, opts={"compare_server_default": True}), metadata
            )
        )
        assert differences == []
        rows = (
            await connection.execute(text("SELECT role, permission FROM role_permissions"))
        ).all()
        assert set(rows) == {
            (role.value, permission.value)
            for role, permitted in ROLE_PERMISSIONS.items()
            for permission in permitted
        }


@pytest.mark.asyncio
async def test_t03_upgrade_preserves_audit_and_pending_owner_confirmation(
    database: AsyncEngine,
) -> None:
    async with database.begin() as connection:
        await connection.run_sync(upgrade, "0003_access")
        await connection.execute(text("INSERT INTO users (telegram_id) VALUES (9001)"))
        await connection.execute(text("INSERT INTO user_roles SELECT id, 'OWNER' FROM users"))
        await connection.execute(
            text("UPDATE shop_settings SET owner_user_id = (SELECT id FROM users)")
        )
        await connection.execute(
            text("""INSERT INTO audit_logs
            (actor_id, action, target_id, correlation_id)
            SELECT id, 'test.t03', id, gen_random_uuid() FROM users""")
        )
        action_id = await connection.scalar(
            text("""INSERT INTO admin_actions
            (id, actor_id, kind, value, expected_version, expires_at)
            SELECT gen_random_uuid(), id, 'settings.support', 'fake_support', 0,
                   now() + interval '10 minutes' FROM users RETURNING id""")
        )
        await connection.run_sync(upgrade)
        assert await connection.scalar(text("SELECT count(*) FROM products")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 1
    service = AccessService(partial(unit_of_work, database))
    assert await service.confirm(9001, action_id)
    assert (await service.settings(9001)).support_username == "fake_support"
