import asyncio
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.application.services.manual import ManualFulfillmentService
from telebotshop.application.services.wallet_checkout import WalletCheckoutService
from telebotshop.domain.errors import InvalidAction, OutOfStock
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import DeliveryResult, DeliveryState
from telebotshop.domain.models.orders import OrderStatus
from telebotshop.domain.models.support import PublicTerms
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import fulfillment_service
from tests.integration.test_core_shop import product_fixture
from tests.integration.test_wallet_v2_ledger import wallet_fixture
from tests.inventory import fake_cipher


def checkout(engine):
    return WalletCheckoutService(
        partial(unit_of_work, engine), enabled=True, terms=PublicTerms("FAKE sandbox terms only")
    )


async def fund_and_price(engine, product, buyer=101, amount=1000):
    wallet = await wallet_fixture(engine)
    await wallet.adjust(9001, buyer, amount, uuid4(), "FAKE sandbox", "FAKE funding")
    service = checkout(engine)
    await service.set_price(9001, product.id, 1000, uuid4(), "FAKE independent credit price")
    return wallet, service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,document",
    [(kind, False) for kind in ProductType] + [(ProductType.REUSABLE_RESOURCE, True)],
)
async def test_credit_checkout_creates_paid_work_without_any_stars_artifacts(
    migrated_database,
    kind,
    document,
):
    engine = migrated_database
    product = await product_fixture(engine, kind, document)
    wallet, service = await fund_and_price(engine, product)
    action = uuid4()
    order = await service.buy(101, product.id, action, 1000, service.terms_version)
    assert order.status == OrderStatus.PAID
    assert order.price_credits == 1000 and order.price_stars is None
    assert await service.buy(101, product.id, action, 1000, service.terms_version) == order
    assert (await wallet.balance(101)).available == 0
    async with engine.connect() as connection:
        for table in ["invoice_intents", "pre_checkout_approvals"]:
            assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM payments WHERE charge_id IS NOT NULL")
            )
            == 0
        )
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1

    # Only the delivery boundary is simulated. No Stars sender, bot, invoice or receipt exists.
    class Sender:
        def __init__(self):
            self.calls = []

        async def send(self, request):
            assert engine.pool.checkedout() == 0
            self.calls.append(request)
            return DeliveryResult(DeliveryState.SENT, message_id=77)

    sender = Sender()
    worker = fulfillment_service(engine, sender)
    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert len(sender.calls) == 1
    if kind == ProductType.MANUAL_FULFILLMENT:
        manual = ManualFulfillmentService(partial(unit_of_work, engine), fake_cipher())
        action = await manual.prepare_completion(
            9001, sender.calls[0].work_id, "FAKE staff completed delivery"
        )
        assert await manual.confirm(9001, action.id)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "DELIVERED"
    assert (await wallet.reconcile(9001)).balanced


@pytest.mark.asyncio
@pytest.mark.parametrize("same_action", [False, True])
async def test_concurrent_credit_buys_cannot_double_debit_or_overspend(
    migrated_database, same_action
):
    product = await product_fixture(migrated_database, ProductType.REUSABLE_RESOURCE, False)
    wallet, service = await fund_and_price(migrated_database, product)
    action = uuid4()
    results = await asyncio.gather(
        service.buy(101, product.id, action, 1000, service.terms_version),
        service.buy(
            101, product.id, action if same_action else uuid4(), 1000, service.terms_version
        ),
        return_exceptions=True,
    )
    if same_action:
        assert results[0] == results[1] and not isinstance(results[0], Exception)
    else:
        assert sum(isinstance(result, InvalidAction) for result in results) == 1
    assert (await wallet.balance(101)).available == 0
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == 1
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM wallet_journals WHERE kind='PURCHASE'")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_credit_checkout_insufficient_funds_rolls_back_order_and_stock(migrated_database):
    engine = migrated_database
    product = await product_fixture(engine, ProductType.UNIQUE_KEY, False)
    wallet, service = await fund_and_price(engine, product, amount=999)
    with pytest.raises(InvalidAction):
        await service.buy(101, product.id, uuid4(), 1000, service.terms_version)
    assert (await wallet.balance(101)).available == 999
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == 0
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM inventory_units WHERE status='AVAILABLE'")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_two_buyers_cannot_buy_last_key(migrated_database):
    engine = migrated_database
    product = await product_fixture(engine, ProductType.UNIQUE_KEY, False)
    wallet, service = await fund_and_price(engine, product)
    await wallet.adjust(9001, 102, 1000, uuid4(), "FAKE sandbox", "FAKE funding")
    results = await asyncio.gather(
        *[
            service.buy(buyer, product.id, uuid4(), 1000, service.terms_version)
            for buyer in (101, 102)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, OutOfStock) for result in results) == 1
    balances = [await wallet.balance(buyer) for buyer in (101, 102)]
    assert sum(balance.available for balance in balances) == 1000


@pytest.mark.asyncio
async def test_credit_checkout_rejects_price_or_terms_changes(migrated_database):
    product = await product_fixture(migrated_database, ProductType.UNIQUE_KEY, False)
    _, service = await fund_and_price(migrated_database, product)
    for price, terms in [(1, service.terms_version), (1000, "stale-terms")]:
        with pytest.raises(InvalidAction):
            await service.buy(101, product.id, uuid4(), price, terms)
