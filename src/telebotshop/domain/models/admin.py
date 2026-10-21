import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.identity import Role


class ActionKind(StrEnum):
    GRANT = "permission.grant"
    REVOKE = "permission.revoke"
    SUPPORT = "settings.support"


class ActionStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    CANCELLED = "cancelled"


@dataclass(frozen=True, repr=False)
class ShopSettings:
    owner_user_id: UUID | None
    support_username: str | None
    version: int


@dataclass(frozen=True, repr=False)
class AdminAction:
    id: UUID
    actor_id: UUID
    kind: ActionKind
    target_id: UUID | None
    role: Role | None
    permission: Permission | None
    value: str | None
    expected_version: int
    expires_at: datetime
    status: ActionStatus = ActionStatus.PENDING


def validate_support_username(value: str | None) -> None:
    if value is not None and (
        not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", value)
    ):
        raise InvalidInput("Invalid support username")
