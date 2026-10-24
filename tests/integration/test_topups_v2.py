import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from hashlib import sha256
from uuid import uuid4

import pytest

from telebotshop.application.services.topups import TopUpService
from telebotshop.domain.models.topups import BankAccount, Settlement, SettlementSource, TopUpState
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.integration.test_wallet_v2_ledger import wallet_fixture


async def topup_fixture(engine):
    wallet = await wallet_fixture(engine)
    service = TopUpService(
        partial(unit_of_work, engine),
        enabled=True,
        bank=BankAccount("FAKEBANK", "FAKE-ACCOUNT"),
        sandbox_rate=Decimal("25000"),
    )
    return wallet, service


def bank_evidence(intent):
    return Settlement(
        "SEPAY",
        "FAKEBANK:FAKE-ACCOUNT:" + uuid4().hex,
        intent.reference,
        "FAKEBANK:FAKE-ACCOUNT",
        "VND",
        Decimal("1000"),
        "",
        "",
        True,
        1,
        1,
        True,
        "in",
        datetime.now(UTC),
        sha256(b"FAKE provider-authenticated evidence").digest(),
        SettlementSource.SIMULATED,
    )


@pytest.mark.asyncio
async def test_bank_topup_credits_once_and_deduplicates_provider_replay(migrated_database):
    wallet, service = await topup_fixture(migrated_database)
    intent = await service.create_bank(101, 1000, uuid4())
    event = bank_evidence(intent)
    assert (await service.ingest(event)).state == TopUpState.CREDITED
    assert (await service.ingest(event)).state == TopUpState.CREDITED
    assert (await wallet.balance(101)).available == 1000
    assert len(await wallet.history(101)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", Decimal("999")),
        ("amount", Decimal("1001")),
        ("recipient", "WRONG:ACCOUNT"),
        ("asset", "USD"),
        ("reference", "UNKNOWN"),
        ("direction", "out"),
        ("occurred_at", datetime.now(UTC) + timedelta(hours=1)),
    ],
)
async def test_bank_mismatches_require_review_without_credit(migrated_database, field, value):
    wallet, service = await topup_fixture(migrated_database)
    intent = await service.create_bank(101, 1000, uuid4())
    result = await service.ingest(replace(bank_evidence(intent), **{field: value}))
    assert result.state == TopUpState.REVIEW_REQUIRED
    assert (await wallet.balance(101)).available == 0


@pytest.mark.asyncio
async def test_usdt_uses_exclusive_buyer_route_and_confirmed_snapshot_quote(migrated_database):
    wallet, service = await topup_fixture(migrated_database)
    await service.set_sandbox_route(
        9001, 101, "FAKE_TESTNET", "FAKE_USDT_CONTRACT", "FAKE-EXCLUSIVE-ADDRESS"
    )
    intent = await service.create_usdt(101, Decimal("0.04"), uuid4())
    event = Settlement(
        "BINANCE",
        "FAKE_TESTNET:FAKE-TX-1",
        None,
        intent.recipient,
        "USDT",
        Decimal("0.04"),
        intent.network,
        intent.token_contract,
        False,
        1,
        3,
        True,
        "in",
        datetime.now(UTC),
        sha256(b"FAKE chain proof").digest(),
        SettlementSource.SIMULATED,
    )
    assert (await service.ingest(event)).state == TopUpState.PENDING_CONFIRMATION
    assert (await wallet.balance(101)).available == 0
    final = replace(event, confirmed=True, confirmations=3)
    assert (await service.ingest(final)).state == TopUpState.CREDITED
    assert (await wallet.balance(101)).available == 1000
    assert intent.rate == Decimal("25000")


@pytest.mark.asyncio
async def test_competing_topup_events_cannot_double_credit_one_intent(migrated_database):
    wallet, service = await topup_fixture(migrated_database)
    intent = await service.create_bank(101, 1000, uuid4())
    results = await asyncio.gather(
        service.ingest(bank_evidence(intent)), service.ingest(bank_evidence(intent))
    )
    assert sum(result.state == TopUpState.CREDITED for result in results) == 1
    assert (await wallet.balance(101)).available == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("first_state", ["CREDITED", "REJECTED"])
async def test_closed_usdt_quote_does_not_block_next_deposit_within_its_original_ttl(
    migrated_database, first_state
):
    from telebotshop.application.services.topup_review import ManualPaymentReviewService

    wallet, service = await topup_fixture(migrated_database)
    await service.set_sandbox_route(9001, 101, "FAKE_TESTNET", "FAKE_TOKEN", "FAKE_ADDRESS")
    original = await service.create_usdt(101, Decimal("0.04"), uuid4())

    def evidence(intent):
        return Settlement(
            "BINANCE",
            "FAKE_TESTNET:" + uuid4().hex,
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
            sha256(b"FAKE verified deposit").digest(),
            SettlementSource.SIMULATED,
        )

    first = evidence(original)
    if first_state == "CREDITED":
        assert (await service.ingest(first)).state == TopUpState.CREDITED
    else:
        result = await service.ingest(replace(first, confirmed=False))
        reviews = ManualPaymentReviewService(partial(unit_of_work, migrated_database))
        action = uuid4()
        await reviews.prepare(
            9001,
            result.event_id,
            original.id,
            action,
            "FAKE rejected deposit",
            "FAKE source report",
            decision="REJECT",
        )
        assert (await reviews.confirm(9001, action)).state == TopUpState.REJECTED
    replacement = await service.create_usdt(101, Decimal("0.04"), uuid4())
    second = evidence(replacement)
    assert second.occurred_at < original.expires_at
    result = await service.ingest(second)
    assert result.state == TopUpState.CREDITED and result.intent_id == replacement.id
    await service.ingest(second)
    await service.ingest(first)
    assert (await wallet.balance(101)).available == (2000 if first_state == "CREDITED" else 1000)
    assert (await wallet.reconcile(9001)).balanced
