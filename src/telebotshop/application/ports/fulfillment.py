from datetime import datetime
from typing import Protocol
from uuid import UUID

from telebotshop.domain.models.fulfillment import (
    DeliveryAttempt,
    DeliveryClaim,
    DeliveryCode,
    DeliveryRequest,
    DeliveryResult,
    DeliveryState,
    EncryptedEntitlement,
    Fulfillment,
)
from telebotshop.domain.models.orders import OrderStatus


class DeliverySender(Protocol):
    async def send(self, request: DeliveryRequest) -> DeliveryResult: ...


class FulfillmentRepository(Protocol):
    async def claimable(self, now: datetime) -> Fulfillment | None: ...
    async def get(self, work_id: UUID, *, for_update: bool = False) -> Fulfillment | None: ...
    async def attempt(self, claim: DeliveryClaim) -> DeliveryAttempt | None: ...
    async def start_claim(self, work: Fulfillment, attempt: DeliveryAttempt) -> None: ...
    async def entitlement(self, work: Fulfillment) -> EncryptedEntitlement | None: ...
    async def start_send(self, claim: DeliveryClaim, now: datetime, deadline: datetime) -> None: ...
    async def finish(
        self,
        claim: DeliveryClaim,
        result: DeliveryResult,
        state: DeliveryState,
        now: datetime,
        due_at: datetime,
        code: DeliveryCode | None,
    ) -> None: ...
    async def abandon(self, claim: DeliveryClaim, now: datetime, code: DeliveryCode) -> None: ...
    async def observe(self, claim: DeliveryClaim, result: DeliveryResult) -> None: ...
    async def expired(self, limit: int, now: datetime) -> tuple[DeliveryClaim, ...]: ...
    async def set_order_state(self, order_id: UUID, state: OrderStatus) -> None: ...
