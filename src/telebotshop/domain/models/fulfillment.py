from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.identity import validate_telegram_id
from telebotshop.domain.models.inventory import (
    InventoryContext,
    LicensePayload,
    SealedPayload,
    SecretPayload,
    UniqueKeyPayload,
)
from telebotshop.domain.models.payments import CommittedWork


class DeliveryState(StrEnum):
    READY = "READY"
    RETRY_WAIT = "RETRY_WAIT"
    CLAIMED = "CLAIMED"
    SENDING = "SENDING"
    SENT = "SENT"
    RETRYABLE = "RETRYABLE"
    UNCERTAIN = "UNCERTAIN"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class DeliveryCode(StrEnum):
    RATE_LIMIT = "RATE_LIMIT"
    REJECTED = "REJECTED"
    NETWORK = "NETWORK"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    INVALID_ENTITLEMENT = "INVALID_ENTITLEMENT"
    CRYPTO = "CRYPTO"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    HELD = "HELD"


@dataclass(frozen=True)
class DeliveryPolicy:
    claim_seconds: int = 30
    send_seconds: int = 60
    timeout_seconds: int = 10
    max_attempts: int = 3
    retry_base_seconds: int = 2
    retry_cap_seconds: int = 60

    def __post_init__(self) -> None:
        values = (
            self.claim_seconds,
            self.send_seconds,
            self.timeout_seconds,
            self.max_attempts,
            self.retry_base_seconds,
            self.retry_cap_seconds,
        )
        if (
            any(type(v) is not int for v in values)
            or not 1 <= self.claim_seconds <= 300
            or not 1 <= self.timeout_seconds <= 20
            or not self.timeout_seconds + 10 <= self.send_seconds <= 300
            or not 1 <= self.max_attempts <= 5
            or not 1 <= self.retry_base_seconds <= self.retry_cap_seconds <= 3600
        ):
            raise InvalidInput("Invalid delivery policy")


@dataclass(frozen=True, repr=False)
class DeliveryClaim:
    work_id: UUID
    order_id: UUID
    attempt_id: UUID

    def __post_init__(self) -> None:
        if not all(isinstance(v, UUID) for v in (self.work_id, self.order_id, self.attempt_id)):
            raise InvalidInput("Invalid delivery claim")


@dataclass(frozen=True, repr=False)
class DeliveryAttempt:
    id: UUID
    work_id: UUID
    state: DeliveryState
    claimed_at: datetime
    lease_expires_at: datetime
    sending_at: datetime | None = None


@dataclass(frozen=True, repr=False)
class Fulfillment:
    work: CommittedWork
    state: DeliveryState
    active_attempt_id: UUID | None
    due_at: datetime
    send_count: int
    hold: bool


@dataclass(frozen=True, repr=False)
class EncryptedEntitlement:
    context: InventoryContext
    sealed: SealedPayload


@dataclass(frozen=True, repr=False)
class DeliveryRequest:
    work_id: UUID
    attempt_id: UUID
    destination_telegram_id: int
    context: InventoryContext
    payload: SecretPayload
    deadline: datetime
    timeout_seconds: int

    def __post_init__(self) -> None:
        validate_telegram_id(self.destination_telegram_id)
        if (
            not isinstance(self.work_id, UUID)
            or not isinstance(self.attempt_id, UUID)
            or not isinstance(self.deadline, datetime)
            or self.deadline.tzinfo is None
            or type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 20
            or not isinstance(self.context, InventoryContext)
            or not (
                self.context.kind == ProductType.UNIQUE_KEY
                and isinstance(self.payload, UniqueKeyPayload)
                or self.context.kind == ProductType.LICENSE
                and isinstance(self.payload, LicensePayload)
            )
        ):
            raise InvalidInput("Invalid delivery request")


@dataclass(frozen=True, repr=False)
class DeliveryResult:
    state: DeliveryState
    message_id: int | None = None
    code: DeliveryCode | None = None
    retry_after: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, DeliveryState) or self.state not in {
            DeliveryState.SENT,
            DeliveryState.RETRYABLE,
            DeliveryState.UNCERTAIN,
            DeliveryState.FAILED,
        }:
            raise InvalidInput("Invalid delivery result")
        if self.state == DeliveryState.SENT:
            valid = (
                type(self.message_id) is int
                and 0 < self.message_id < 2**63
                and self.code is None
                and self.retry_after is None
            )
        else:
            valid = self.message_id is None and isinstance(self.code, DeliveryCode)
            if self.state == DeliveryState.RETRYABLE:
                valid = (
                    valid
                    and self.code == DeliveryCode.RATE_LIMIT
                    and type(self.retry_after) is int
                    and 1 <= self.retry_after <= 86400
                )
            else:
                valid = valid and self.retry_after is None
        if not valid:
            raise InvalidInput("Invalid delivery result")
