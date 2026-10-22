import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

from telebotshop.application.ports.inventory_crypto import InventoryCipher
from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.application.services.access import require_permission
from telebotshop.application.services.orders import validate_uuid
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import Page, ProductType, validate_page
from telebotshop.domain.models.fulfillment import DeliveryState, Fulfillment
from telebotshop.domain.models.manual import (
    ManualAction,
    ManualDetail,
    ManualEvidence,
    ManualEvidenceContext,
    ManualWorkView,
)
from telebotshop.domain.models.orders import Order, OrderStatus
from telebotshop.domain.models.resources import ManualPayload, validate_resource_payload


async def locked_manual(work: UnitOfWork, work_id: UUID) -> tuple[Order, Fulfillment]:
    fulfillment = await work.fulfillment.get(work_id)
    if fulfillment is None:
        raise InvalidAction("Manual work unavailable")
    order = await work.orders.order(fulfillment.work.order_id, for_update=True)
    fulfillment = await work.fulfillment.get(work_id, for_update=True)
    if order is None or fulfillment is None:
        raise InvalidAction("Manual work unavailable")
    return order, fulfillment


async def require_waiting(work: UnitOfWork, order: Order, fulfillment: Fulfillment) -> None:
    committed = fulfillment.work
    if (
        committed.product_type != ProductType.MANUAL_FULFILLMENT
        or fulfillment.state != DeliveryState.AWAITING_MANUAL
        or fulfillment.hold
        or fulfillment.active_attempt_id is None
        or committed.resource_id is None
        or order.status != OrderStatus.FULFILLING
        or order.attention_reason is not None
        or order.buyer_id != committed.buyer_id
        or order.item_id != committed.order_item_id
        or order.product_type != committed.product_type
        or order.unit_id is not None
        or await work.payments.primary(order.id) != committed.payment_id
    ):
        raise InvalidAction("Manual work not awaiting completion")


class ManualFulfillmentService:
    def __init__(self, uow: UnitOfWorkFactory, cipher: InventoryCipher) -> None:
        self.uow, self.cipher = uow, cipher

    async def queue(self, actor_id: int, page: int = 0) -> Page[ManualWorkView]:
        validate_page(page)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            await require_permission(work, actor_id, Permission.MANAGE_FULFILLMENT)
            return await work.manual.queue(page)

    async def _detail(
        self, work: UnitOfWork, order: Order, fulfillment: Fulfillment
    ) -> ManualDetail:
        await require_waiting(work, order, fulfillment)
        encrypted = await work.fulfillment.entitlement(fulfillment)
        if encrypted is None or encrypted.metadata is None:
            raise InvalidAction("Manual entitlement unavailable")
        payload = self.cipher.open(encrypted.context, encrypted.sealed)
        if not isinstance(payload, ManualPayload):
            raise InvalidAction("Invalid manual entitlement")
        validate_resource_payload(fulfillment.work.product_type, encrypted.metadata, payload)
        return ManualDetail(
            fulfillment.work.id,
            order.id,
            fulfillment.work.destination_telegram_id,
            order.sku,
            fulfillment.state,
            payload.customer_notice,
            payload.staff_instructions,
        )

    async def detail(self, actor_id: int, work_id: UUID) -> ManualDetail:
        validate_uuid(work_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await require_permission(work, actor_id, Permission.MANAGE_FULFILLMENT)
            order, fulfillment = await locked_manual(work, work_id)
            result = await self._detail(work, order, fulfillment)
            await work.manual.audit(actor.user_id, order.id, work_id, "manual.viewed", uuid4())
            await work.commit()
            return result

    async def prepare_completion(
        self, actor_id: int, work_id: UUID, reference: str
    ) -> ManualAction:
        validate_uuid(work_id)
        evidence = ManualEvidence(reference)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await require_permission(work, actor_id, Permission.MANAGE_FULFILLMENT)
            order, fulfillment = await locked_manual(work, work_id)
            await self._detail(work, order, fulfillment)
            await work.inventory.check_keys(self.cipher.proof())
            action = ManualAction(
                uuid4(),
                actor.user_id,
                work_id,
                fulfillment.active_attempt_id,
                await work.orders.now() + timedelta(minutes=10),
            )
            context = ManualEvidenceContext(order.product_id, order.sku, work_id, action.id)
            await work.manual.save(action, self.cipher.seal(context, evidence))
            await work.commit()
            return action

    async def confirm(self, actor_id: int, action_id: UUID) -> bool:
        validate_uuid(action_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await require_permission(work, actor_id, Permission.MANAGE_FULFILLMENT)
            action = await work.manual.action(action_id)
            if action is None or action.actor_id != actor.user_id:
                raise InvalidAction("Manual confirmation unavailable")
            order, fulfillment = await locked_manual(work, action.work_id)
            action = await work.manual.action(action_id, for_update=True)
            if action.status == ActionStatus.APPLIED:
                return False
            await require_waiting(work, order, fulfillment)
            if (
                action.status != ActionStatus.PENDING
                or action.expires_at <= await work.orders.now()
                or action.attempt_id != fulfillment.active_attempt_id
            ):
                raise InvalidAction("Manual confirmation expired or changed")
            context = ManualEvidenceContext(order.product_id, order.sku, action.work_id, action.id)
            evidence = self.cipher.open(context, await work.manual.evidence(action.id))
            if not isinstance(evidence, ManualEvidence):
                raise InvalidAction("Invalid completion evidence")
            await work.manual.finish(action)
            await work.fulfillment.set_order_state(order.id, OrderStatus.DELIVERED)
            await work.manual.audit(
                actor.user_id,
                order.id,
                action.work_id,
                "manual.completed",
                action.id,
                hashlib.sha256(evidence.reference.encode("utf-8")).hexdigest(),
            )
            await work.commit()
            return True

    async def cancel(self, actor_id: int, action_id: UUID) -> None:
        validate_uuid(action_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await require_permission(work, actor_id, Permission.MANAGE_FULFILLMENT)
            action = await work.manual.action(action_id, for_update=True)
            if (
                action is None
                or action.actor_id != actor.user_id
                or action.status == ActionStatus.APPLIED
            ):
                raise InvalidAction("Manual confirmation unavailable")
            if action.status == ActionStatus.PENDING:
                await work.manual.finish(action, cancelled=True)
                await work.commit()
