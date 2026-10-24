from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.application.services.payment_reconciliation import PaymentReconciliationService
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.topups import Settlement, SettlementSource, TopUpState
from tests.fulfillment import timed_uow
from tests.integration.test_topups_v2 import bank_evidence, topup_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("asset", "USDC"),
        ("network", "WRONG_NETWORK"),
        ("token_contract", "WRONG_TOKEN"),
        ("recipient", "WRONG_RECIPIENT"),
        ("amount", Decimal("0.039")),
        ("amount", Decimal("0.041")),
        ("token_verified", False),
        ("confirmations", 2),
        ("confirmed", False),
        ("occurred_at", datetime.now(UTC) + timedelta(hours=1)),
    ],
)
async def test_usdt_unverified_or_mismatched_settlement_never_credits(
    migrated_database, field, value
):
    wallet, topups = await topup_fixture(migrated_database)
    await topups.set_sandbox_route(9001, 101, "FAKE_NETWORK", "FAKE_TOKEN", "FAKE_ADDRESS")
    intent = await topups.create_usdt(101, Decimal("0.04"), uuid4())
    event = Settlement(
        "BINANCE",
        "FAKE_NETWORK:FAKE_TX",
        None,
        intent.recipient,
        "USDT",
        intent.amount,
        intent.network,
        intent.token_contract,
        True,
        3,
        3,
        True,
        "in",
        datetime.now(UTC),
        sha256(b"FAKE trusted chain proof").digest(),
        SettlementSource.SIMULATED,
    )
    result = await topups.ingest(replace(event, **{field: value}))
    assert result.state in {TopUpState.REVIEW_REQUIRED, TopUpState.PENDING_CONFIRMATION}
    assert (await wallet.balance(101)).available == 0
    # A later configured FX rate cannot replace the immutable quote.
    topups.sandbox_rate = Decimal("30000")
    assert (await topups.history(101))[0].rate == Decimal("25000")


@pytest.mark.asyncio
async def test_modified_provider_transaction_is_durable_conflict_without_second_credit(
    migrated_database,
):
    wallet, topups = await topup_fixture(migrated_database)
    intent = await topups.create_bank(101, 1000, uuid4())
    event = bank_evidence(intent)
    await topups.ingest(event)
    conflict = await topups.ingest(replace(event, amount=Decimal("1001")))
    assert conflict.reason == "CONFLICT" and (await wallet.balance(101)).available == 1000
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT conflict FROM settlement_events")) is True
        assert await connection.scalar(text("SELECT count(*) FROM wallet_journals")) == 1


@pytest.mark.asyncio
async def test_reconciler_can_backfill_missed_history_with_explicit_window(migrated_database):
    _, topups = await topup_fixture(migrated_database)
    now = datetime.now(UTC)
    since = now - timedelta(hours=6)
    windows = []

    class Reader:
        provider = "SEPAY"

        async def read(self, after, before, page):
            assert migrated_database.pool.checkedout() == 0
            windows.append((after, before, page))
            return ()

    from telebotshop.infrastructure.database.uow import unit_of_work

    service = PaymentReconciliationService(partial(unit_of_work, migrated_database), topups)
    await service.run(Reader(), since=since)
    assert windows[0][0] == since - timedelta(minutes=5)
    assert windows[0][1] == since + timedelta(hours=1)
    with pytest.raises(InvalidAction):
        await service.run(Reader(), since=now + timedelta(hours=1))


@pytest.mark.asyncio
async def test_unused_expired_usdt_quote_can_be_replaced_without_rebinding_old_deposit(
    migrated_database,
):
    wallet, topups = await topup_fixture(migrated_database)
    await topups.set_sandbox_route(9001, 101, "FAKE_NETWORK", "FAKE_TOKEN", "FAKE_ADDRESS")
    original = await topups.create_usdt(101, Decimal("0.04"), uuid4())
    topups.uow = timed_uow(migrated_database, original.expires_at + timedelta(minutes=1))
    replacement = await topups.create_usdt(101, Decimal("0.04"), uuid4())
    assert replacement.id != original.id
    old = next(t for t in await topups.history(101) if t.id == original.id)
    assert old.state == TopUpState.REJECTED
    event = Settlement(
        "BINANCE",
        "FAKE_NETWORK:FAKE_DELAYED_TX",
        None,
        original.recipient,
        "USDT",
        original.amount,
        original.network,
        original.token_contract,
        True,
        3,
        3,
        True,
        "in",
        original.created_at,
        sha256(b"FAKE delayed chain proof").digest(),
        SettlementSource.SIMULATED,
    )
    result = await topups.ingest(event)
    assert result.state == TopUpState.REVIEW_REQUIRED and result.intent_id != replacement.id
    assert (await wallet.balance(101)).available == 0


@pytest.mark.asyncio
async def test_expired_quote_with_detected_deposit_waits_for_original_finality(migrated_database):
    wallet, topups = await topup_fixture(migrated_database)
    await topups.set_sandbox_route(9001, 101, "FAKE_NETWORK", "FAKE_TOKEN", "FAKE_ADDRESS")
    original = await topups.create_usdt(101, Decimal("0.04"), uuid4())
    event = Settlement(
        "BINANCE",
        "FAKE_NETWORK:FAKE_FINALITY_TX",
        None,
        original.recipient,
        "USDT",
        original.amount,
        original.network,
        original.token_contract,
        False,
        1,
        3,
        True,
        "in",
        original.created_at,
        sha256(b"FAKE pending chain proof").digest(),
        SettlementSource.SIMULATED,
    )
    assert (await topups.ingest(event)).state == TopUpState.PENDING_CONFIRMATION
    topups.uow = timed_uow(migrated_database, original.expires_at + timedelta(minutes=1))
    with pytest.raises(InvalidAction):
        await topups.create_usdt(101, Decimal("0.04"), uuid4())
    result = await topups.ingest(replace(event, confirmed=True, confirmations=3))
    assert result.intent_id == original.id and result.state == TopUpState.CREDITED
    assert (await wallet.balance(101)).available == 1000
