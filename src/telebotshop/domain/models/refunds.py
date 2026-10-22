import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.identity import validate_telegram_id
from telebotshop.domain.models.payments import PaymentReceipt, validate_receipt_text


class RefundState(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    CANCELLED = "CANCELLED"


def require_utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise InvalidInput("Invalid refund timestamp")


@dataclass(frozen=True, repr=False)
class RefundEvidence:
    reference: str

    def __post_init__(self):
        validate_receipt_text(self.reference, 500)


@dataclass(frozen=True)
class RefundEvidenceContext:
    payment_id: UUID
    action_id: UUID

    def __post_init__(self):
        if any(not isinstance(value, UUID) for value in (self.payment_id, self.action_id)):
            raise InvalidInput("Invalid refund evidence context")


def refund_evidence_from_record(record: object) -> RefundEvidence:
    if not isinstance(record, dict) or set(record) != {"reference"}:
        raise InvalidInput("Invalid refund evidence")
    return RefundEvidence(record["reference"])


@dataclass(frozen=True, repr=False)
class RefundReceipt:
    charge_id: str
    currency: str
    amount: int
    payload: str
    provider_charge_id: str = ""
    source: str = "EVENT"

    def __post_init__(self):
        PaymentReceipt(
            self.charge_id, None, self.currency, self.amount, self.payload, self.provider_charge_id
        )
        if type(self.amount) is not int or self.amount <= 0:
            raise InvalidInput("Invalid refund amount")
        if self.source not in {"EVENT", "TRANSACTION"}:
            raise InvalidInput("Invalid refund source")

    def digest(self) -> bytes:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        ).digest()


@dataclass(frozen=True, repr=False)
class RefundRequest:
    id: UUID
    charge_id: str
    payer_id: int
    deadline: datetime
    timeout_seconds: float = 10.0

    def __post_init__(self):
        if not isinstance(self.id, UUID):
            raise InvalidInput("Invalid refund request")
        validate_receipt_text(self.charge_id, 1024)
        validate_telegram_id(self.payer_id)
        require_utc(self.deadline)
        if type(self.timeout_seconds) not in {int, float} or not 0 < self.timeout_seconds <= 10:
            raise InvalidInput("Invalid refund timeout")


@dataclass(frozen=True)
class RefundResult:
    state: RefundState

    def __post_init__(self):
        if not isinstance(self.state, RefundState) or self.state not in {
            RefundState.SUCCEEDED,
            RefundState.FAILED,
            RefundState.UNCERTAIN,
        }:
            raise InvalidInput("Invalid refund result")


@dataclass(frozen=True, repr=False)
class StarEvidence:
    charge_id: str
    payer_id: int
    amount: int
    payload: str | None
    refunded: bool
    created_at: datetime

    def __post_init__(self):
        validate_receipt_text(self.charge_id, 1024)
        validate_telegram_id(self.payer_id)
        if type(self.amount) is not int or not 0 < self.amount < 2**63:
            raise InvalidInput("Invalid transaction evidence")
        if self.payload is not None:
            validate_receipt_text(self.payload, 128, empty=True)
        if type(self.refunded) is not bool:
            raise InvalidInput("Invalid transaction direction")
        require_utc(self.created_at)


@dataclass(frozen=True)
class RefundSnapshot:
    order_status: str | None = None
    work_id: str | None = None
    work_state: str | None = None
    attempt_id: str | None = None
    send_count: int | None = None
    hold: bool | None = None
    resend_id: str | None = None
    resend_state: str | None = None

    def record(self):
        return asdict(self)


@dataclass(frozen=True, repr=False)
class RefundIntent:
    id: UUID
    payment_id: UUID
    actor_id: UUID | None
    expected: RefundSnapshot
    accept_uncertain: bool
    expires_at: datetime
    status: RefundState = RefundState.PENDING
    started_at: datetime | None = None
    deadline: datetime | None = None
    completed_at: datetime | None = None
    confirmed_by: str | None = None
    observed_state: RefundState | None = None


@dataclass(frozen=True)
class RefundView:
    payment_id: UUID
    order_id: UUID | None
    amount: int
    currency: str
    primary: bool
    verified: bool
    state: RefundState | None
    intent_id: UUID | None
    snapshot: RefundSnapshot
