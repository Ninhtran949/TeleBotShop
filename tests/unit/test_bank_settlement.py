import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.infrastructure.payments.sepay import SePayWebhookVerifier

NOW = datetime(2026, 10, 24, tzinfo=UTC)
SECRET = b"FAKE-sepay-sandbox-secret-not-a-real-credential"


def signed(raw, timestamp=None):
    timestamp = str(int(NOW.timestamp())) if timestamp is None else timestamp
    signature = hmac.new(SECRET, timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return {"X-SePay-Timestamp": timestamp, "X-SePay-Signature": "sha256=" + signature}


def payload():
    return {
        "id": 12,
        "gateway": "FAKEBANK",
        "transactionDate": "2026-10-24 07:00:00",
        "accountNumber": "FAKE-ACCOUNT",
        "referenceCode": "FAKE-BANK-TX-1",
        "content": "TB0123456789ABCDEF01234567",
        "code": None,
        "transferType": "in",
        "transferAmount": 1000,
    }


def test_sepay_verifies_raw_body_timestamp_and_parses_stable_bank_identity():
    raw = json.dumps(payload(), indent=2).encode()
    result = SePayWebhookVerifier(SECRET).verify(raw, signed(raw), NOW)
    assert result.transaction_id == "FAKEBANK:FAKE-ACCOUNT:FAKE-BANK-TX-1"
    assert result.reference == payload()["content"]
    assert result.amount == 1000 and result.confirmed


@pytest.mark.parametrize(
    "case", ["mutated", "stale", "future", "unsigned", "duplicate_keys", "fraction", "huge"]
)
def test_sepay_rejects_untrusted_or_invalid_webhooks(case):
    value = payload()
    if case == "fraction":
        value["transferAmount"] = 1.5
    raw = json.dumps(value).encode()
    if case == "duplicate_keys":
        raw = raw[:-1] + b',"transferAmount":1000}'
    if case == "huge":
        raw = b" " * 65537
    headers = signed(raw)
    if case == "mutated":
        raw = raw.replace(b"1000", b"2000")
    elif case in {"stale", "future"}:
        headers = signed(raw, str(int(NOW.timestamp()) + (-301 if case == "stale" else 301)))
    elif case == "unsigned":
        headers = {}
    with pytest.raises(InvalidInput):
        SePayWebhookVerifier(SECRET).verify(raw, headers, NOW)
