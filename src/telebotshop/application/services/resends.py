from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

from telebotshop.application.ports.fulfillment import DeliverySender
from telebotshop.application.ports.inventory_crypto import InventoryCipher
from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.application.services.access import require_permission
from telebotshop.application.services.entitlements import read_entitlement
from telebotshop.application.services.history import history_scope
from telebotshop.application.services.orders import validate_uuid
from telebotshop.domain.errors import AccessDenied, InvalidAction, InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import (
    DeliveryCode,
    DeliveryPolicy,
    DeliveryRequest,
    DeliveryResult,
    DeliveryState,
    Fulfillment,
)
from telebotshop.domain.models.history import ResendAction, ResendState
from telebotshop.domain.models.identity import validate_telegram_id
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.domain.models.orders import Order, OrderStatus


async def resend_scope(work: UnitOfWork, actor_id: int, staff: bool) -> UUID | None:
    buyer_id = await history_scope(work, actor_id, staff)
    if staff:
        await require_permission(work, actor_id, Permission.MANAGE_FULFILLMENT)
    return buyer_id


async def lock_order(
    work: UnitOfWork, order_id: UUID, buyer_id: UUID | None
) -> tuple[Order, Fulfillment]:
    order = await work.orders.order(order_id, for_update=True)
    if order is None or buyer_id is not None and order.buyer_id != buyer_id:
        raise AccessDenied("Order unavailable")
    view = await work.history.detail(order_id, buyer_id)
    fulfillment = (
        await work.fulfillment.get(view.work_id, for_update=True) if view and view.work_id else None
    )
    if fulfillment is None:
        raise InvalidAction("Delivered entitlement unavailable")
    return order, fulfillment


def require_delivered(order: Order, fulfillment: Fulfillment) -> None:
    if (
        order.status != OrderStatus.DELIVERED
        or order.attention_reason is not None
        or fulfillment.state != DeliveryState.SENT
        or fulfillment.hold
        or fulfillment.active_attempt_id is None
        or fulfillment.work.product_type == ProductType.MANUAL_FULFILLMENT
    ):
        raise InvalidAction("Order cannot be resent")


class ResendService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        cipher: InventoryCipher,
        sender: DeliverySender,
        policy: DeliveryPolicy | None = None,
    ) -> None:
        self.uow, self.cipher, self.sender = uow, cipher, sender
        self.policy = policy or DeliveryPolicy()

    async def prepare(self, actor_id: int, order_id: UUID, *, staff: bool = False) -> ResendAction:
        validate_uuid(order_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            buyer_id = await resend_scope(work, actor_id, staff)
            order, fulfillment = await lock_order(work, order_id, buyer_id)
            require_delivered(order, fulfillment)
            profile = await work.identity.get_profile(actor_id)
            now = await work.orders.now()
            active = await work.history.active_resend(fulfillment.work.id)
            if active is not None:
                if active.status == ResendState.PENDING and active.expires_at <= now:
                    await work.history.finish_resend(active, ResendState.CANCELLED, now)
                    await work.history.audit_resend(active, order.id, "resend.expired")
                else:
                    if active.status == ResendState.SENDING and active.deadline <= now:
                        await work.history.finish_resend(active, ResendState.UNCERTAIN, now)
                        await work.history.audit_resend(active, order.id, "resend.uncertain")
                        await work.commit()
                    elif (
                        active.status == ResendState.PENDING
                        and active.actor_id == profile.id
                        and active.staff == staff
                    ):
                        return active
                    raise InvalidAction("Resend already pending or requires reconciliation")
            action = ResendAction(
                uuid4(),
                profile.id,
                fulfillment.work.id,
                fulfillment.active_attempt_id,
                staff,
                now + timedelta(minutes=10),
            )
            await work.history.save_resend(action)
            await work.history.audit_resend(action, order.id, "resend.prepared")
            await work.commit()
            return action

    async def _locked_action(
        self, work: UnitOfWork, actor_id: int, action_id: UUID
    ) -> tuple[Order, Fulfillment, ResendAction]:
        validate_telegram_id(actor_id)
        validate_uuid(action_id)
        profile = await work.identity.get_profile(actor_id)
        action = await work.history.resend(action_id)
        if profile is None or action is None or action.actor_id != profile.id:
            raise AccessDenied("Resend unavailable")
        buyer_id = await resend_scope(work, actor_id, action.staff)
        fulfillment = await work.fulfillment.get(action.work_id)
        order, fulfillment = await lock_order(work, fulfillment.work.order_id, buyer_id)
        action = await work.history.resend(action.id, for_update=True)
        return order, fulfillment, action

    async def cancel(self, actor_id: int, action_id: UUID) -> None:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            order, _, action = await self._locked_action(work, actor_id, action_id)
            if action.status == ResendState.CANCELLED:
                return
            if action.status != ResendState.PENDING:
                raise InvalidAction("Resend already started")
            await work.history.finish_resend(action, ResendState.CANCELLED, await work.orders.now())
            await work.history.audit_resend(action, order.id, "resend.cancelled")
            await work.commit()

    async def begin(self, actor_id: int, action_id: UUID) -> DeliveryRequest | ResendAction:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            order, fulfillment, action = await self._locked_action(work, actor_id, action_id)
            now = await work.orders.now()
            if action.status != ResendState.PENDING:
                if action.status == ResendState.SENDING and action.deadline <= now:
                    await work.history.finish_resend(action, ResendState.UNCERTAIN, now)
                    await work.history.audit_resend(action, order.id, "resend.uncertain")
                    await work.commit()
                    return replace(action, status=ResendState.UNCERTAIN)
                return action
            if action.expires_at <= now:
                await work.history.finish_resend(action, ResendState.CANCELLED, now)
                await work.history.audit_resend(action, order.id, "resend.expired")
                await work.commit()
                return replace(action, status=ResendState.CANCELLED)
            require_delivered(order, fulfillment)
            if fulfillment.active_attempt_id != action.attempt_id:
                raise InvalidAction("Delivery changed")
            deadline = now + timedelta(seconds=self.policy.send_seconds)
            await work.history.start_resend(action, now, deadline)
            code = DeliveryCode.INVALID_ENTITLEMENT
            request = None
            try:
                material = await read_entitlement(work, order, fulfillment, self.cipher)
                if material is not None:
                    context, payload = material
                    request = DeliveryRequest(
                        fulfillment.work.id,
                        action.id,
                        fulfillment.work.destination_telegram_id,
                        context,
                        payload,
                        deadline,
                        self.policy.timeout_seconds,
                    )
            except (InventoryCryptoError, InvalidInput):
                code = DeliveryCode.CRYPTO
            if request is None:
                await work.history.finish_resend(
                    replace(action, status=ResendState.SENDING),
                    ResendState.FAILED,
                    now,
                    DeliveryResult(DeliveryState.FAILED, code=code),
                )
                await work.history.audit_resend(action, order.id, "resend.failed")
            else:
                await work.history.audit_resend(action, order.id, "resend.sending")
            updated = request or await work.history.resend(action.id)
            await work.commit()
            return updated

    async def complete(self, action_id: UUID, result: DeliveryResult) -> ResendAction:
        validate_uuid(action_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            action = await work.history.resend(action_id)
            if action is None:
                raise InvalidAction("Resend unavailable")
            fulfillment = await work.fulfillment.get(action.work_id)
            order, _ = await lock_order(work, fulfillment.work.order_id, None)
            action = await work.history.resend(action.id, for_update=True)
            if action.status not in {ResendState.SENDING, ResendState.UNCERTAIN}:
                return action
            now = await work.orders.now()
            state = ResendState.FAILED
            if (
                action.status == ResendState.UNCERTAIN
                or action.deadline <= now
                or result.state == DeliveryState.UNCERTAIN
            ):
                state = ResendState.UNCERTAIN
            elif result.state == DeliveryState.SENT:
                state = ResendState.SENT
            if await work.history.finish_resend(action, state, now, result):
                event = (
                    "resend.observed"
                    if action.status == ResendState.UNCERTAIN
                    else f"resend.{state.value.lower()}"
                )
                await work.history.audit_resend(action, order.id, event)
            updated = await work.history.resend(action.id)
            await work.commit()
            return updated

    async def confirm(self, actor_id: int, action_id: UUID) -> ResendAction:
        prepared = await self.begin(actor_id, action_id)
        if isinstance(prepared, ResendAction):
            return prepared
        result = await self.sender.send(prepared)
        return await self.complete(action_id, result)
