import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.wallet_checkout import WalletCheckoutService
from telebotshop.application.services.wallet_refunds import WalletRefundService
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.catalog import CategoryDraft, ProductType, SaleState
from telebotshop.domain.models.coupons import Coupon, CouponQuote, DiscountKind
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.integration.test_core_shop import product_fixture
from tests.integration.test_coupons import coupons, draft
from tests.integration.test_wallet_v2_checkout import fund_and_price
from tests.orders import order_service
from tests.payments import payment_service, receipt
from tests.pre_checkout import payable_invoice
from tests.resources import resource_payload, resource_service


async def fixture(engine, *, kind=ProductType.REUSABLE_RESOURCE, amount=3000, **config):
    product = await product_fixture(engine, kind, False)
    wallet, checkout = await fund_and_price(engine, product, amount=amount)
    await coupons(engine).create(9001, draft(**config), uuid4())
    quote = await coupons(engine).preview(101, product.draft.sku, "fake10")
    return product, wallet, checkout, quote


async def other_product(engine, product):
    catalog = CatalogService(partial(unit_of_work, engine))
    changed = replace(product.draft, sku="FAKE-SECOND")
    action = await catalog.prepare_product(9001, changed)
    await catalog.confirm(9001, action.id)
    action = await catalog.prepare_sale(9001, changed.sku, SaleState.ACTIVE)
    await catalog.confirm(9001, action.id)
    resources = resource_service(engine)
    action = await resources.prepare(9001, changed.sku, resource_payload(changed.product_type))
    await resources.confirm(9001, action.id)
    return await catalog.admin_product(9001, changed.sku)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ProductType))
async def test_coupon_net_payment_snapshot_is_atomic_for_every_product_type(
    migrated_database, kind
):
    engine = migrated_database
    product, wallet, checkout, quote = await fixture(engine, kind=kind)
    order = await checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote)
    assert order.price_credits == 900 and order.price_stars is None and order.status == "PAID"
    assert (await wallet.balance(101)).available == 2100
    async with unit_of_work(engine) as work:
        assert await work.coupons.redemption(order.id) == quote
    async with engine.connect() as connection:
        assert (
            await connection.execute(
                text("SELECT base_credits,discount_credits,total_credits FROM coupon_redemptions")
            )
        ).one() == (1000, 100, 900)
        assert await connection.scalar(text("SELECT amount FROM payments")) == 900
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action='coupon.redeemed'")
            )
            == 1
        )
        assert (
            await connection.scalar(text("SELECT count(*) FROM fulfillments WHERE state='READY'"))
            == 1
        )
        assert await connection.scalar(text("SELECT count(*) FROM invoice_intents")) == 0
    assert (await wallet.reconcile(9001)).balanced


@pytest.mark.asyncio
async def test_fixed_credit_discount_uses_integer_snapshot(migrated_database):
    product, wallet, checkout, quote = await fixture(migrated_database, kind=ProductType.UNIQUE_KEY)
    await coupons(migrated_database).create(
        9001, draft(code="FIXED", kind=DiscountKind.FIXED, value=123), uuid4()
    )
    quote = await coupons(migrated_database).preview(101, product.draft.sku, "FIXED")
    order = await checkout.buy(101, product.id, uuid4(), 877, checkout.terms_version, coupon=quote)
    assert order.price_credits == 877 and quote.discount_credits == 123
    assert (await wallet.balance(101)).available == 2123


@pytest.mark.asyncio
async def test_coupon_double_click_and_disabled_replay_debit_once(migrated_database):
    engine = migrated_database
    product, wallet, checkout, quote = await fixture(engine, usage_cap=1)
    action = uuid4()
    results = await asyncio.gather(
        *[
            checkout.buy(101, product.id, action, 900, checkout.terms_version, coupon=quote)
            for _ in range(2)
        ]
    )
    assert results[0] == results[1]
    await coupons(engine).set_active(9001, "FAKE10", False, uuid4(), expected_active=True)
    assert (
        await checkout.buy(101, product.id, action, 900, checkout.terms_version, coupon=quote)
        == results[0]
    )
    with pytest.raises(InvalidAction):
        await checkout.buy(
            101,
            product.id,
            action,
            900,
            checkout.terms_version,
            coupon=replace(quote, coupon_id=uuid4()),
        )
    assert (await wallet.balance(101)).available == 2100
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM coupon_redemptions")) == 1
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM wallet_journals WHERE kind='PURCHASE'")
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("same_buyer", [False, True])
async def test_coupon_global_and_per_user_caps_across_different_products(
    migrated_database, same_buyer
):
    engine = migrated_database
    product, wallet, checkout, quote = await fixture(engine, usage_cap=2 if same_buyer else 1)
    other = await other_product(engine, product)
    await checkout.set_price(9001, other.id, 1000, uuid4(), "FAKE independent price")
    buyer = 101 if same_buyer else 102
    if not same_buyer:
        await wallet.adjust(9001, 102, 3000, uuid4(), "FAKE buyer2 funding", "FAKE")
    other_quote = await coupons(engine).preview(buyer, other.draft.sku, "FAKE10")
    results = await asyncio.gather(
        checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote),
        checkout.buy(buyer, other.id, uuid4(), 900, checkout.terms_version, coupon=other_quote),
        return_exceptions=True,
    )
    assert sum(isinstance(result, InvalidAction) for result in results) == 1
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM coupon_redemptions")) == 1
    assert (await wallet.reconcile(9001)).balanced


@pytest.mark.asyncio
async def test_last_coupon_use_holds_lock_across_two_physical_transactions(migrated_database):
    engine = migrated_database
    product, wallet, checkout, quote = await fixture(engine, usage_cap=1)
    other = await other_product(engine, product)
    await checkout.set_price(9001, other.id, 1000, uuid4(), "FAKE price")
    await wallet.adjust(9001, 102, 3000, uuid4(), "FAKE buyer2", "FAKE")
    other_quote = await coupons(engine).preview(102, other.draft.sku, "FAKE10")
    locked, release = asyncio.Event(), asyncio.Event()
    pids = []

    @asynccontextmanager
    async def held():
        async with unit_of_work(engine) as work:
            pids.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
            get = work.coupons.get

            async def hold(coupon_id, *, for_update=False):
                result = await get(coupon_id, for_update=for_update)
                if for_update:
                    locked.set()
                    await release.wait()
                return result

            work.coupons.get = hold
            yield work

    @asynccontextmanager
    async def limited():
        async with unit_of_work(engine) as work:
            pids.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
            configure = work.orders.configure_timeouts

            async def short_timeout():
                await configure()
                await work.session.execute(text("SET LOCAL lock_timeout='100ms'"))

            work.orders.configure_timeouts = short_timeout
            yield work

    one = WalletCheckoutService(held, enabled=True, terms=None)
    one.terms_version = checkout.terms_version
    two = WalletCheckoutService(limited, enabled=True, terms=None)
    two.terms_version = checkout.terms_version
    buying = asyncio.create_task(
        one.buy(101, product.id, uuid4(), 900, one.terms_version, coupon=quote)
    )
    try:
        await asyncio.wait_for(locked.wait(), 3)
        with pytest.raises(DBAPIError) as blocked:
            await two.buy(102, other.id, uuid4(), 900, two.terms_version, coupon=other_quote)
        assert blocked.value.orig.sqlstate == "55P03" and len(set(pids)) == 2
    finally:
        release.set()
        await asyncio.wait_for(buying, 3)
    with pytest.raises(InvalidAction):
        await checkout.buy(102, other.id, uuid4(), 900, checkout.terms_version, coupon=other_quote)
    assert (await wallet.balance(102)).available == 3000


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["funds", "payment", "audit", "commit"])
async def test_coupon_checkout_failure_rolls_back_usage_debit_order_and_stock(
    migrated_database, failure
):
    engine = migrated_database
    amount = 899 if failure == "funds" else 3000
    product, wallet, checkout, quote = await fixture(
        engine, kind=ProductType.UNIQUE_KEY, amount=amount
    )

    @asynccontextmanager
    async def failing():
        async with unit_of_work(engine) as work:
            target = (
                work.credit_orders
                if failure == "payment"
                else work.wallet
                if failure == "audit"
                else work
            )
            name = "pay" if failure == "payment" else "audit" if failure == "audit" else "commit"
            original = getattr(target, name)

            async def fail(*args, **kwargs):
                if failure != "commit":
                    await original(*args, **kwargs)
                raise OSError("FAKE known pre-commit failure")

            if failure != "funds":
                setattr(target, name, fail)
            yield work

    checkout.uow = failing
    with pytest.raises(InvalidAction if failure == "funds" else OSError):
        await checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote)
    assert (await wallet.balance(101)).available == amount
    async with engine.connect() as connection:
        for table in ("orders", "coupon_redemptions", "payments", "fulfillments"):
            assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM wallet_journals WHERE kind='PURCHASE'")
            )
            == 0
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action='coupon.redeemed'")
            )
            == 0
        )
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "AVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["price", "disabled", "forged"])
async def test_stale_or_forged_coupon_quote_never_falls_back_to_full_price(
    migrated_database, change
):
    engine = migrated_database
    product, wallet, checkout, quote = await fixture(engine)
    if change == "price":
        await checkout.set_price(9001, product.id, 1001, uuid4(), "FAKE price changed")
    elif change == "disabled":
        await coupons(engine).set_active(9001, "FAKE10", False, uuid4(), expected_active=True)
    else:
        quote = replace(quote, base_credits=1001, discount_credits=101)
    with pytest.raises(InvalidAction):
        await checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote)
    assert (await wallet.balance(101)).available == 3000
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM coupon_redemptions")) == 0


@pytest.mark.asyncio
async def test_credit_refund_returns_net_amount_without_restoring_coupon_usage(migrated_database):
    engine = migrated_database
    product, wallet, checkout, quote = await fixture(engine, usage_cap=1)
    action = uuid4()
    order = await checkout.buy(101, product.id, action, 900, checkout.terms_version, coupon=quote)
    refunds = WalletRefundService(partial(unit_of_work, engine))
    refund = await refunds.prepare(9001, order.id, uuid4(), "FAKE refund policy", "FAKE evidence")
    entry = await refunds.confirm(9001, refund)
    assert entry.delta == 900 and (await wallet.balance(101)).available == 3000
    with pytest.raises(InvalidAction):
        await checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote)
    replay = await checkout.buy(101, product.id, action, 900, checkout.terms_version, coupon=quote)
    assert replay.status == "REFUNDED"
    async with unit_of_work(engine) as work:
        assert await work.coupons.redemption(order.id) == quote
        assert (await work.coupons.uses(quote.coupon_id, order.buyer_id)) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope", ["product", "category", "wrong_product", "wrong_category", "expired"]
)
async def test_coupon_preview_and_buy_validate_scope_and_expiry_from_server(
    migrated_database, scope
):
    engine = migrated_database
    product, wallet, checkout, _ = await fixture(engine)
    config = draft(code="SCOPED")
    if scope in {"product", "wrong_product"}:
        target = product if scope == "product" else await other_product(engine, product)
        config = replace(config, product_id=target.id)
    elif scope in {"category", "wrong_category"}:
        category = product.draft.category_code
        if scope == "wrong_category":
            catalog = CatalogService(partial(unit_of_work, engine))
            action = await catalog.prepare_category(9001, CategoryDraft("OTHER", "FAKE other"))
            await catalog.confirm(9001, action.id)
            category = "OTHER"
        config = replace(config, category_code=category)
    if scope == "expired":
        # A valid historical coupon is already expired; never weaken the immutable-config guard.
        config = replace(config, expires_at=datetime.now(UTC) - timedelta(days=1))
        async with unit_of_work(engine) as work:
            owner = await work.access.get(9001)
            expired = Coupon(uuid4(), config, True, owner.user_id)
            await work.coupons.save(expired)
            await work.commit()
        quote = CouponQuote(expired.id, config.code, product.id, 1000, 100)
    else:
        coupon = await coupons(engine).create(9001, config, uuid4())
        quote = CouponQuote(coupon.id, config.code, product.id, 1000, 100)
    if scope in {"product", "category"}:
        assert await coupons(engine).preview(101, product.draft.sku, config.code) == quote
        await checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote)
        assert (await wallet.balance(101)).available == 2100
    else:
        with pytest.raises(InvalidAction):
            await coupons(engine).preview(101, product.draft.sku, config.code)
        with pytest.raises(InvalidAction):
            await checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote)
        assert (await wallet.balance(101)).available == 3000


@pytest.mark.asyncio
async def test_missing_coupon_audit_forces_database_rollback(migrated_database):
    engine = migrated_database
    product, wallet, checkout, quote = await fixture(engine)

    @asynccontextmanager
    async def missing_audit():
        async with unit_of_work(engine) as work:
            work.coupons._audit = AsyncMock()
            yield work

    checkout.uow = missing_audit
    with pytest.raises(DBAPIError, match="net payment and audit"):
        await checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote)
    assert (await wallet.balance(101)).available == 3000
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM coupon_redemptions")) == 0


@pytest.mark.asyncio
async def test_coupon_committed_purchase_with_lost_ack_replays_without_new_debit(migrated_database):
    engine = migrated_database
    product, wallet, checkout, quote = await fixture(engine, usage_cap=1)
    action = uuid4()
    original = checkout.uow

    @asynccontextmanager
    async def lost_ack():
        async with original() as work:
            commit = work.commit

            async def commit_then_fail():
                await commit()
                raise OSError("FAKE lost commit acknowledgement")

            work.commit = commit_then_fail
            yield work

    checkout.uow = lost_ack
    with pytest.raises(OSError):
        await checkout.buy(101, product.id, action, 900, checkout.terms_version, coupon=quote)
    checkout.uow = original
    order = await checkout.buy(101, product.id, action, 900, checkout.terms_version, coupon=quote)
    assert order.status == "PAID" and (await wallet.balance(101)).available == 2100
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM coupon_redemptions")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == 1


@pytest.mark.asyncio
async def test_historical_late_stars_receipt_preserves_coupon_usage_and_credit_snapshot(
    migrated_database,
):
    engine = migrated_database
    product, _, checkout, quote = await fixture(engine, usage_cap=1)
    historical = await order_service(engine).create(101, product.id, uuid4())
    await payable_invoice(engine, historical)
    order = await checkout.buy(101, product.id, uuid4(), 900, checkout.terms_version, coupon=quote)
    await payment_service(engine).record(receipt(historical))
    async with unit_of_work(engine) as work:
        assert await work.coupons.redemption(order.id) == quote
        assert await work.coupons.uses(quote.coupon_id, order.buyer_id) == (1, 1)
        assert (await work.orders.order(order.id)).price_credits == 900
        assert (await work.orders.order(historical.id)).price_stars == historical.price_stars
