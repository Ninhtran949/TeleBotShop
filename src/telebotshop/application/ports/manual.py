from typing import Protocol
from uuid import UUID

from telebotshop.domain.models.catalog import Page
from telebotshop.domain.models.inventory import SealedPayload
from telebotshop.domain.models.manual import ManualAction, ManualWorkView


class ManualRepository(Protocol):
    async def queue(self, page: int) -> Page[ManualWorkView]: ...
    async def save(self, action: ManualAction, sealed: SealedPayload) -> None: ...
    async def action(self, action_id: UUID, *, for_update: bool = False) -> ManualAction | None: ...
    async def evidence(self, action_id: UUID) -> SealedPayload: ...
    async def finish(self, action: ManualAction, *, cancelled: bool = False) -> None: ...
    async def audit(
        self,
        actor_id: UUID,
        order_id: UUID,
        work_id: UUID,
        action: str,
        correlation_id: UUID,
        evidence_digest: str | None = None,
    ) -> None: ...
