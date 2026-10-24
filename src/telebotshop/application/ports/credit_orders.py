from typing import Protocol
from uuid import UUID

from telebotshop.domain.models.orders import Order


class CreditOrderRepository(Protocol):
    async def price(self, product_id: UUID) -> int | None: ...
    async def set_price(
        self, product_id: UUID, amount: int, actor_id: UUID, action_id: UUID, reason: str
    ) -> None: ...
    async def pay(self, order: Order, journal_id: UUID, telegram_id: int) -> UUID: ...
    async def refund_action(self, action_id: UUID, *, for_update: bool = False): ...
    async def save_refund(
        self,
        action_id: UUID,
        order_id: UUID,
        actor_id: UUID,
        reason: str,
        source: bytes,
        expected: bytes,
        expires_at,
    ) -> None: ...
    async def apply_refund(
        self, action_id: UUID, journal_id: UUID, order_id: UUID, work_id: UUID
    ) -> None: ...
    async def reverse_topup(
        self, topup_id: UUID, journal_id: UUID, actor_id: UUID, reason: str, source: bytes
    ) -> None: ...
    async def external_refunds(self, topup_id: UUID): ...
    async def record_external_refund(
        self,
        action_id: UUID,
        topup_id: UUID,
        actor_id: UUID,
        state: str,
        reason: str,
        source: bytes,
    ) -> None: ...
