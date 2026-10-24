import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.payments import validate_receipt_text


class TopUpState(StrEnum):
    DETECTED = "DETECTED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CREDITED = "CREDITED"
    REJECTED = "REJECTED"


class SettlementSource(StrEnum):
    SEPAY_WEBHOOK = "SEPAY_WEBHOOK"
    SEPAY_API = "SEPAY_API"
    BINANCE_API = "BINANCE_API"
    ONCHAIN = "ONCHAIN"
    SIMULATED = "SIMULATED"


@dataclass(frozen=True, repr=False)
class BankAccount:
    bank: str
    account: str

    def __post_init__(self):
        validate_receipt_text(self.bank, 32)
        validate_receipt_text(self.account, 100)

    @property
    def recipient(self):
        return self.bank + ":" + self.account


@dataclass(frozen=True, repr=False)
class TopUpIntent:
    id: UUID
    buyer_id: UUID
    action_id: UUID
    rail: str
    reference: str
    credit_amount: int
    amount: Decimal
    asset: str
    recipient: str
    network: str
    token_contract: str
    route_id: UUID | None
    rate: Decimal
    rate_source: str
    created_at: datetime
    expires_at: datetime
    state: TopUpState = TopUpState.DETECTED
    event_id: UUID | None = None
    journal_id: UUID | None = None


@dataclass(frozen=True, repr=False)
class Settlement:
    provider: str
    transaction_id: str
    reference: str | None
    recipient: str
    asset: str
    amount: Decimal
    network: str
    token_contract: str
    confirmed: bool
    confirmations: int
    required_confirmations: int
    token_verified: bool
    direction: str
    occurred_at: datetime
    proof_digest: bytes
    source: SettlementSource

    def __post_init__(self):
        if self.provider not in {"SEPAY", "BINANCE"}:
            raise InvalidInput("Unsupported settlement provider")
        for value, limit in [(self.transaction_id, 300), (self.recipient, 200), (self.asset, 16)]:
            validate_receipt_text(value, limit)
        for value in [self.network, self.token_contract]:
            validate_receipt_text(value, 200, empty=True)
        if self.reference is not None:
            validate_receipt_text(self.reference, 100)
        if (
            not isinstance(self.amount, Decimal)
            or not self.amount.is_finite()
            or not 0 < self.amount < Decimal("1e15")
            or self.amount.as_tuple().exponent < -18
            or type(self.confirmed) is not bool
            or type(self.token_verified) is not bool
            or type(self.confirmations) is not int
            or not 0 <= self.confirmations <= 1000000
            or type(self.required_confirmations) is not int
            or not 1 <= self.required_confirmations <= 1000000
            or self.direction not in {"in", "out"}
            or not isinstance(self.source, SettlementSource)
            or not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or not isinstance(self.proof_digest, bytes)
            or len(self.proof_digest) != 32
        ):
            raise InvalidInput("Invalid settlement evidence")

    def facts(self):
        return {
            "provider": self.provider,
            "transaction_id": self.transaction_id,
            "reference": self.reference,
            "recipient": self.recipient,
            "asset": self.asset,
            "amount": format(self.amount.normalize(), "f"),
            "network": self.network,
            "token_contract": self.token_contract,
            "direction": self.direction,
            "occurred_at": self.occurred_at.isoformat(),
        }

    def digest(self):
        return sha256(
            json.dumps(self.facts(), sort_keys=True, separators=(",", ":")).encode()
        ).digest()


@dataclass(frozen=True, repr=False)
class SettlementResult:
    event_id: UUID
    intent_id: UUID | None
    state: TopUpState
    reason: str | None = None
