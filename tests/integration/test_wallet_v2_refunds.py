import asyncio
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.application.services.wallet_refunds import WalletRefundService
from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.catalog import ProductType
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import fulfillment_service
from tests.integration.test_core_shop import product_fixture
from tests.integration.test_topups_v2 import bank_evidence, topup_fixture
from tests.integration.test_wallet_v2_checkout import fund_and_price


@pytest.mark.asyncio
async def test_credit_refund_compensates_once_and_keeps_key_allocated(migrated_database):
    engine = migrated_database
    product = await product_fixture(engine, ProductType.UNIQUE_KEY, False)
    wallet, checkout = await fund_and_price(engine, product)
    order = await checkout.buy(101, product.id, uuid4(), 1000, checkout.terms_version)
    service = WalletRefundService(partial(unit_of_work, engine))
    action = await service.prepare(
        9001, order.id, uuid4(), "FAKE refund review", "FAKE support evidence"
    )
    await service.confirm(9001, action)
    await service.confirm(9001, action)
    assert (await wallet.balance(101)).available == 1000
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "REFUNDED"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
        assert await connection.scalar(text("SELECT hold FROM fulfillments")) is True
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM wallet_journals WHERE kind='REFUND'")
            )
            == 1
        )
    assert (await wallet.reconcile(9001)).balanced


@pytest.mark.asyncio
async def test_credit_refund_rejects_unauthorized_and_inflight_delivery(migrated_database):
    engine = migrated_database
    product = await product_fixture(engine, ProductType.UNIQUE_KEY, False)
    wallet, checkout = await fund_and_price(engine, product)
    order = await checkout.buy(101, product.id, uuid4(), 1000, checkout.terms_version)
    service = WalletRefundService(partial(unit_of_work, engine))
    with pytest.raises(AccessDenied):
        await service.prepare(101, order.id, uuid4(), "FAKE", "FAKE source")
    worker = fulfillment_service(engine, None)
    claim = await worker.claim()
    assert await worker.prepare(claim) is not None
    with pytest.raises(InvalidAction):
        await service.prepare(9001, order.id, uuid4(), "FAKE", "FAKE source")
    assert (await wallet.balance(101)).available == 0


@pytest.mark.asyncio
async def test_external_cash_refund_is_manual_evidence_and_unknown_is_never_success(
    migrated_database,
):
    engine = migrated_database
    wallet, topups = await topup_fixture(engine)
    intent = await topups.create_bank(101, 1000, uuid4())
    await topups.ingest(bank_evidence(intent))
    service = WalletRefundService(partial(unit_of_work, engine))

    async def record(state, action=None, actor=9001):
        return await service.record_external_refund(
            actor,
            intent.id,
            action or uuid4(),
            state,
            "FAKE cash refund review",
            "FAKE source report",
        )

    with pytest.raises(AccessDenied):
        await record("REQUESTED", actor=101)
    await record("REQUESTED")
    uncertain_action = uuid4()
    await record("UNCERTAIN", uncertain_action)
    await record("UNCERTAIN", uncertain_action)
    assert (await wallet.balance(101)).available == 1000
    with pytest.raises(InvalidAction):
        await record("CONFIRMED")
    await service.reverse_topup(9001, intent.id, "FAKE reversal before cash refund", "FAKE source")
    await record("CONFIRMED")
    with pytest.raises(InvalidAction):
        await record("REQUESTED")
    assert (await wallet.balance(101)).available == 0
    async with engine.connect() as connection:
        states = (
            (
                await connection.execute(
                    text("SELECT state FROM external_refund_records ORDER BY created_at,id")
                )
            )
            .scalars()
            .all()
        )
        assert states == ["REQUESTED", "UNCERTAIN", "CONFIRMED"]
    assert (await wallet.reconcile(9001)).balanced


@pytest.mark.asyncio
async def test_credit_refund_races_worker_preparation_without_send_or_release(migrated_database):
    engine = migrated_database
    product = await product_fixture(engine, ProductType.UNIQUE_KEY, False)
    wallet, checkout = await fund_and_price(engine, product)
    order = await checkout.buy(101, product.id, uuid4(), 1000, checkout.terms_version)
    refunds = WalletRefundService(partial(unit_of_work, engine))
    worker = fulfillment_service(engine, None)
    claim = await worker.claim()
    action = await refunds.prepare(9001, order.id, uuid4(), "FAKE refund", "FAKE support")
    result, prepared = await asyncio.gather(
        refunds.confirm(9001, action), worker.prepare(claim), return_exceptions=True
    )
    async with engine.connect() as connection:
        status = await connection.scalar(text("SELECT status FROM orders"))
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
        if status == "REFUNDED":
            assert not isinstance(result, Exception) and prepared is None
            assert (await wallet.balance(101)).available == 1000
        else:
            assert isinstance(result, InvalidAction) and prepared is not None
            assert await connection.scalar(text("SELECT state FROM fulfillments")) == "SENDING"
            assert (await wallet.balance(101)).available == 0
