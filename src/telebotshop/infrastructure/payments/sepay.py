import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.topups import BankAccount, Settlement, SettlementSource


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate settlement fields")
    return result


def parse_bank_event(value, raw, source):
    try:
        amount = Decimal(str(value["transferAmount"]))
        if amount != amount.to_integral_value():
            raise ValueError
        account = value["accountNumber"]
        bank = value["gateway"]
        reference = value["referenceCode"]
        if not all(isinstance(v, str) and v.strip() for v in [account, bank, reference]):
            raise ValueError
        contents = " ".join(str(value.get(field) or "") for field in ["code", "content"])
        references = set(re.findall(r"(?<![A-Za-z0-9])TB[0-9A-F]{24}(?![A-Za-z0-9])", contents))
        occurred = datetime.fromisoformat(value["transactionDate"])
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone(timedelta(hours=7)))
        return Settlement(
            "SEPAY",
            bank + ":" + account + ":" + reference,
            next(iter(references)) if len(references) == 1 else None,
            bank + ":" + account,
            "VND",
            amount,
            "",
            "",
            True,
            1,
            1,
            True,
            value["transferType"],
            occurred.astimezone(UTC),
            hashlib.sha256(raw).digest(),
            source,
        )
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise InvalidInput("Invalid SePay settlement") from None


class SePayWebhookVerifier:
    def __init__(self, secret: bytes):
        if not isinstance(secret, bytes) or not 32 <= len(secret) <= 256:
            raise InvalidInput("Invalid sandbox webhook secret")
        self.secret = secret

    def verify(self, raw: bytes, headers: dict[str, str], now: datetime) -> Settlement:
        headers = {key.lower(): value for key, value in headers.items()}
        timestamp = headers.get("x-sepay-timestamp", "")
        signature = headers.get("x-sepay-signature", "")
        if (
            not isinstance(raw, bytes)
            or len(raw) > 65536
            or now.tzinfo is None
            or re.fullmatch(r"[0-9]{10,12}", timestamp) is None
            or re.fullmatch(r"sha256=[0-9a-f]{64}", signature) is None
        ):
            raise InvalidInput("Invalid SePay authentication")
        if abs(int(now.timestamp()) - int(timestamp)) > 300:
            raise InvalidInput("Expired SePay authentication")
        expected = (
            "sha256="
            + hmac.new(self.secret, timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(expected, signature):
            raise InvalidInput("Invalid SePay authentication")
        try:
            value = json.loads(raw, object_pairs_hook=unique_object, parse_float=Decimal)
        except (ValueError, UnicodeError, RecursionError):
            raise InvalidInput("Invalid SePay body") from None
        return parse_bank_event(value, raw, SettlementSource.SEPAY_WEBHOOK)


def bank_qr_url(bank: BankAccount, amount: int, reference: str) -> str:
    return "https://qr.sepay.vn/img?" + urlencode(
        {"bank": bank.bank, "acc": bank.account, "amount": amount, "des": reference}
    )
