from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput

MAX_CREDITS = 10**15 - 1


def credits(value: int) -> int:
    if type(value) is not int or not 0 < value <= MAX_CREDITS:
        raise InvalidInput("Invalid credit amount")
    return value


def quote_credits(amount: Decimal, rate: Decimal) -> int:
    if any(not isinstance(v, Decimal) or not v.is_finite() or v <= 0 for v in (amount, rate)):
        raise InvalidInput("Invalid sandbox quote")
    try:
        with localcontext() as ctx:
            ctx.prec = 50
            value = (amount * rate).quantize(Decimal(1), rounding=ROUND_DOWN)
            if value > MAX_CREDITS:
                raise InvalidInput("Quote exceeds supported amount")
            return credits(int(value))
    except (InvalidOperation, OverflowError):
        raise InvalidInput("Invalid sandbox quote") from None


class JournalKind(StrEnum):
    ADJUSTMENT = "ADJUSTMENT"
    TOPUP = "TOPUP"
    PURCHASE = "PURCHASE"
    REFUND = "REFUND"
    REVERSAL = "REVERSAL"


class WalletPermission(StrEnum):
    ADJUST = "ADJUST"
    REVIEW = "REVIEW"
    REFUND = "REFUND"
    AUDIT = "AUDIT"
    PRICE = "PRICE"


@dataclass(frozen=True, repr=False)
class WalletBalance:
    available: int
    pending: int = 0


@dataclass(frozen=True, repr=False)
class LedgerEntry:
    id: UUID
    operation_key: str
    kind: JournalKind
    subject_id: UUID
    user_id: UUID
    delta: int
    actor_id: UUID | None
    reason: str
    evidence: bytes
    created_at: datetime


@dataclass(frozen=True)
class LedgerReconciliation:
    balanced: bool
    mismatched_accounts: int
