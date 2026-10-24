import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx2 as httpx
import pytest
from fastapi import FastAPI

from telebotshop.api.bank_webhook import create_bank_webhook_router
from telebotshop.infrastructure.payments.bank import SePaySandboxReader
from telebotshop.infrastructure.payments.binance import (
    BinanceDepositReader,
    NetworkPolicy,
    deposit_evidence,
)
from telebotshop.infrastructure.payments.sepay import SePayWebhookVerifier


@pytest.mark.asyncio
async def test_bank_endpoint_never_acknowledges_failed_commit_or_echoes_error():
    async def dispatch(raw, headers):
        raise RuntimeError("FAKE-private-provider-secret")

    app = FastAPI()
    app.include_router(create_bank_webhook_router(dispatch))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/payments/sepay/webhook",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-SePay-Signature": "sha256=" + "0" * 64,
                "X-SePay-Timestamp": "1792800000",
            },
        )
    assert response.status_code == 503
    assert "FAKE-private" not in response.text


@pytest.mark.asyncio
async def test_binance_adapter_signs_only_read_endpoint_and_is_disabled_by_default():
    calls = []

    async def request(url, headers, query):
        from urllib.parse import urlencode

        signature = query.pop("signature")
        assert hmac.compare_digest(
            signature,
            hmac.new(b"FAKE-secret", urlencode(query).encode(), hashlib.sha256).hexdigest(),
        )
        assert "/capital/deposit/hisrec" in url
        calls.append(url)
        return []

    policy = NetworkPolicy("FAKE_TESTNET", "FAKE_TOKEN", 3)
    reader = BinanceDepositReader("FAKE-key", b"FAKE-secret", policy, request=request)
    from telebotshop.domain.errors import InvalidAction

    with pytest.raises(InvalidAction):
        await reader.read(datetime.now(UTC), datetime.now(UTC), 1)
    assert calls == []
    reader.enabled = True
    assert await reader.read(datetime.now(UTC), datetime.now(UTC), 1) == ()
    assert len(calls) == 1


def test_exchange_history_alone_does_not_prove_usdt_token_contract():
    row = {
        "network": "FAKE_TESTNET",
        "txId": "FAKE-TX",
        "address": "FAKE-ADDRESS",
        "coin": "USDT",
        "amount": "0.04",
        "status": 1,
        "travelRuleStatus": 0,
        "confirmTimes": "3/3",
        "insertTime": 1792800000000,
    }
    event = deposit_evidence(row, NetworkPolicy("FAKE_TESTNET", "FAKE_TOKEN", 3))
    assert event.amount == Decimal("0.04") and event.confirmed
    assert event.token_verified is False


@pytest.mark.asyncio
async def test_sepay_api_and_webhook_share_canonical_identity_and_rate_limit():
    now = datetime.now(UTC)
    content = "TB" + "A" * 24
    calls = []
    row = {
        "id": "FAKE-api-uuid-distinct-from-webhook-id",
        "bank_brand_name": "FAKEBANK",
        "account_number": "FAKE-ACCOUNT",
        "reference_number": "FAKE-REFERENCE",
        "transaction_date": now.isoformat(),
        "transfer_type": "in",
        "amount_in": 1000,
        "amount_out": 0,
        "transaction_content": content,
    }

    async def request(url, headers, query):
        assert url == "https://userapi-sandbox.sepay.vn/v2/transactions"
        assert headers == {"Authorization": "Bearer FAKE-token"}
        assert query["per_page"] == "100" and query["timestamp_format"] == "iso8601"
        calls.append(time.monotonic())
        return {"status": "success", "data": [row]}

    reader = SePaySandboxReader("FAKE-token", request=request)
    results = [await reader.read(now, now, page) for page in (1, 2, 3)]
    assert calls[1] - calls[0] >= 0.3 and calls[2] - calls[1] >= 0.3
    body = json.dumps(
        {
            "id": 123,
            "gateway": "FAKEBANK",
            "accountNumber": "FAKE-ACCOUNT",
            "referenceCode": "FAKE-REFERENCE",
            "transactionDate": now.isoformat(),
            "transferType": "in",
            "transferAmount": 1000,
            "content": content,
        }
    ).encode()
    key = b"FAKE-key-for-sandbox-HMAC-testing-only"
    timestamp = str(int(now.timestamp()))
    signature = (
        "sha256=" + hmac.new(key, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )
    webhook = SePayWebhookVerifier(key).verify(
        body,
        {
            "X-SePay-Timestamp": timestamp,
            "X-SePay-Signature": signature,
        },
        now,
    )
    assert results[0][0].transaction_id == webhook.transaction_id
    assert results[0][0].digest() == webhook.digest()
