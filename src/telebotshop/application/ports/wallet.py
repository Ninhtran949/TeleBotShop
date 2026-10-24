from typing import Protocol
from uuid import UUID

from telebotshop.domain.models.wallet import (
    JournalKind,
    LedgerEntry,
    LedgerReconciliation,
    WalletPermission,
)


class WalletRepository(Protocol):
    async def ensure_account(self, user_id: UUID) -> UUID: ...
    async def permission(self, user_id: UUID, permission: WalletPermission) -> bool: ...
    async def grant(self, user_id: UUID, permission: WalletPermission, allowed: bool) -> None: ...
    async def balance(self, user_id: UUID) -> int: ...
    async def by_operation(self, key: str) -> LedgerEntry | None: ...
    async def post(
        self,
        key: str,
        kind: JournalKind,
        subject_id: UUID,
        user_id: UUID,
        delta: int,
        actor_id: UUID | None,
        reason: str,
        evidence: bytes,
    ) -> LedgerEntry: ...
    async def history(self, user_id: UUID, offset: int) -> tuple[LedgerEntry, ...]: ...
    async def reconcile(self) -> LedgerReconciliation: ...
    async def audit(
        self,
        actor_id: UUID | None,
        target_id: UUID,
        event: str,
        correlation_id: UUID,
        changes: dict[str, str | bool] | None = None,
    ) -> None: ...
