from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telebotshop.domain.models.catalog import ProductType
from telebotshop.infrastructure.database.models.coupons import metadata
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.integration.test_core_shop import product_fixture
from tests.integration.test_coupon_checkout import fixture
from tests.integration.test_notification_migrations import migrate, snapshot
from tests.integration.test_wallet_v2_checkout import fund_and_price
from tests.orders import order_service


@pytest.mark.asyncio
async def test_coupon_upgrade_and_empty_downgrade_preserve_every_predecessor_row_and_function(
    database,
):
    engine = database
    await migrate(engine, "0020_external_refund_records")
    product = await product_fixture(engine, ProductType.REUSABLE_RESOURCE, False)
    _, checkout = await fund_and_price(engine, product)
    await checkout.buy(101, product.id, uuid4(), 1000, checkout.terms_version)
    async with engine.connect() as connection:
        tables = (
            await connection.scalars(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() "
                    "AND tablename<>'alembic_version' ORDER BY tablename"
                )
            )
        ).all()
        functions = dict(
            (
                await connection.execute(
                    text(
                        "SELECT proname,pg_get_functiondef(oid) FROM pg_proc "
                        "WHERE pronamespace=current_schema()::regnamespace"
                    )
                )
            ).all()
        )
    before = await snapshot(engine, tables)
    for revision, down in [
        ("head", False),
        ("0020_external_refund_records", True),
        ("head", False),
    ]:
        await migrate(engine, revision, down=down)
        assert await snapshot(engine, tables) == before
        async with engine.connect() as connection:
            after = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT proname,pg_get_functiondef(oid) FROM pg_proc "
                            "WHERE pronamespace=current_schema()::regnamespace"
                        )
                    )
                ).all()
            )
        assert all(after[name] == body for name, body in functions.items())
    async with engine.connect() as connection:
        differences = await connection.run_sync(
            lambda c: compare_metadata(
                MigrationContext.configure(c, opts={"compare_server_default": True}),
                metadata,
            )
        )
    assert differences == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE coupons SET value=value+1",
        "UPDATE coupons SET usage_cap=usage_cap+1",
        "DELETE FROM coupons",
        "TRUNCATE coupons CASCADE",
        "UPDATE coupon_redemptions SET discount_credits=discount_credits+1",
        "DELETE FROM coupon_redemptions",
        "TRUNCATE coupon_redemptions",
    ],
)
async def test_database_preserves_coupon_configuration_and_redemption_history(
    migrated_database, statement
):
    engine = migrated_database
    product, wallet, checkout, quote = await fixture(engine)
    await checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote)
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(text(statement))
    with pytest.raises(DBAPIError, match="forward migration"):
        await migrate(engine, "0020_external_refund_records", down=True)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM coupon_redemptions")) == 1
    assert (await wallet.balance(101)).available == 2100


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["stars", "wallet_full_price"])
async def test_redemption_cannot_attach_to_unpaid_stars_or_full_price_order(
    migrated_database, method
):
    engine = migrated_database
    product, _, checkout, quote = await fixture(engine, kind=ProductType.UNIQUE_KEY)
    order = (
        await order_service(engine).create(101, product.id, uuid4())
        if method == "stars"
        else await checkout.buy(101, product.id, uuid4(), 1000, checkout.terms_version)
    )
    with pytest.raises(DBAPIError, match="net payment and audit"):
        async with unit_of_work(engine) as work:
            await work.coupons.redeem(order, quote)
            await work.commit()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM coupon_redemptions")) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("per_user", [False, True])
async def test_database_caps_reject_redemption_even_when_application_checks_are_bypassed(
    migrated_database, per_user
):
    engine = migrated_database
    product, wallet, checkout, quote = await fixture(engine, usage_cap=2 if per_user else 1)
    await checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote)
    buyer = 101 if per_user else 102
    if not per_user:
        await wallet.adjust(9001, buyer, 1000, uuid4(), "FAKE buyer2", "FAKE funding")
    full_price = await checkout.buy(buyer, product.id, uuid4(), 1000, checkout.terms_version)
    with pytest.raises(DBAPIError, match="usage unavailable"):
        async with unit_of_work(engine) as work:
            await work.coupons.redeem(full_price, quote)
            await work.commit()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM coupon_redemptions")) == 1
