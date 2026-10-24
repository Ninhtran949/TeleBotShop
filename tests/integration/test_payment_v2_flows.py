import asyncio
import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from uuid import uuid4

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from telebotshop.api.bank_webhook import create_bank_webhook_router
from telebotshop.application.services.payment_reconciliation import PaymentReconciliationService
from telebotshop.application.services.topup_review import ManualPaymentReviewService
from telebotshop.domain.errors import AccessDenied
from telebotshop.domain.models.topups import TopUpState
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.payments.binance import NetworkPolicy, SimulatedDepositReader
from telebotshop.infrastructure.payments.sepay import SePayWebhookVerifier
from tests.integration.test_topups_v2 import bank_evidence, topup_fixture


@pytest.mark.asyncio
async def test_signed_sandbox_bank_webhook_to_real_postgres_credit_is_replay_safe(
    migrated_database,
):
    wallet, topups = await topup_fixture(migrated_database)
    intent = await topups.create_bank(101, 1000, uuid4())
    now = datetime.now(UTC)
    body = json.dumps(
        {
            "gateway": "FAKEBANK",
            "accountNumber": "FAKE-ACCOUNT",
            "referenceCode": "FAKE-HTTP-TX",
            "transactionDate": now.isoformat(),
            "content": intent.reference,
            "transferType": "in",
            "transferAmount": 1000,
        }
    ).encode()
    key = b"FAKE-sandbox-HMAC-key-with-minimum-32-bytes"
    timestamp = str(int(now.timestamp()))
    signature = (
        "sha256=" + hmac.new(key, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )
    verifier = SePayWebhookVerifier(key)

    async def dispatch(raw, headers):
        return await topups.ingest(verifier.verify(raw, headers, datetime.now(UTC)))

    app = FastAPI()
    app.include_router(create_bank_webhook_router(dispatch))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(2):
            result = await client.post(
                "/payments/sepay/webhook",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-SePay-Signature": signature,
                    "X-SePay-Timestamp": timestamp,
                },
            )
            assert result.status_code == 200
    assert (await wallet.balance(101)).available == 1000


@pytest.mark.asyncio
async def test_simulated_binance_reconciler_credits_one_exclusive_buyer_quote(migrated_database):
    wallet, topups = await topup_fixture(migrated_database)
    await topups.set_sandbox_route(9001, 101, "FAKE_TESTNET", "FAKE_TOKEN", "FAKE-ADDRESS")
    from decimal import Decimal

    await topups.create_usdt(101, Decimal("0.04"), uuid4())
    reader = SimulatedDepositReader(
        [
            {
                "network": "FAKE_TESTNET",
                "txId": "FAKE-CHAIN-TX",
                "address": "FAKE-ADDRESS",
                "coin": "USDT",
                "amount": "0.04",
                "status": 1,
                "travelRuleStatus": 0,
                "confirmTimes": "3/3",
                "insertTime": int(datetime.now(UTC).timestamp() * 1000),
                "sandbox_token_contract": "FAKE_TOKEN",
            }
        ],
        NetworkPolicy("FAKE_TESTNET", "FAKE_TOKEN", 3),
    )
    service = PaymentReconciliationService(partial(unit_of_work, migrated_database), topups)
    await service.run(reader)
    await service.run(reader)
    assert (await wallet.balance(101)).available == 1000
    assert len(await wallet.history(101)) == 1


@pytest.mark.asyncio
async def test_manual_mapping_requires_current_actor_bound_confirmation_and_source(
    migrated_database,
):
    wallet, topups = await topup_fixture(migrated_database)
    intent = await topups.create_bank(101, 1000, uuid4())
    event = replace(bank_evidence(intent), reference="FAKE-UNMATCHED-CONTENT")
    stored = await topups.ingest(event)
    reviews = ManualPaymentReviewService(partial(unit_of_work, migrated_database))
    action = uuid4()
    with pytest.raises(AccessDenied):
        await reviews.prepare(
            101, stored.event_id, intent.id, action, "FAKE reason", "FAKE bank audit"
        )
    await reviews.prepare(
        9001,
        stored.event_id,
        intent.id,
        action,
        "FAKE independently matched bank source",
        "FAKE-bank-audit",
    )
    with pytest.raises(AccessDenied):
        await reviews.confirm(101, action)
    assert (await reviews.confirm(9001, action)).state == TopUpState.CREDITED
    assert (await reviews.confirm(9001, action)).state == TopUpState.CREDITED
    assert (await topups.ingest(event)).state == TopUpState.CREDITED
    assert (await wallet.balance(101)).available == 1000


@pytest.mark.asyncio
async def test_auto_and_manual_approval_race_produces_one_credit(migrated_database):
    wallet, topups = await topup_fixture(migrated_database)
    intent = await topups.create_bank(101, 1000, uuid4())
    event = bank_evidence(intent)
    stored = await topups.ingest(replace(event, confirmed=False))
    reviews = ManualPaymentReviewService(partial(unit_of_work, migrated_database))
    action = uuid4()
    await reviews.prepare(9001, stored.event_id, intent.id, action, "FAKE review", "FAKE source")
    await asyncio.gather(topups.ingest(event), reviews.confirm(9001, action))
    assert (await wallet.balance(101)).available == 1000
    async with migrated_database.connect() as connection:
        assert (
            await connection.scalar(text("SELECT count(*) FROM wallet_journals WHERE kind='TOPUP'"))
            == 1
        )
