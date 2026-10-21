import logging
from datetime import timedelta
from random import uniform
from uuid import uuid4

from telebotshop.application.ports.fulfillment import DeliverySender
from telebotshop.application.ports.inventory_crypto import InventoryCipher
from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.fulfillment import (
    DeliveryAttempt,
    DeliveryClaim,
    DeliveryCode,
    DeliveryPolicy,
    DeliveryRequest,
    DeliveryResult,
    DeliveryState,
    Fulfillment,
)
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.domain.models.orders import Order, OrderStatus

logger = logging.getLogger(__name__)


async def locked_attempt(
    work: UnitOfWork,
    claim: DeliveryClaim,
) -> tuple[Order, Fulfillment, DeliveryAttempt] | None:
    order = await work.orders.order(claim.order_id, for_update=True)
    fulfillment = await work.fulfillment.get(claim.work_id, for_update=True)
    if order is None or fulfillment is None or fulfillment.work.order_id != order.id:
        return None
    attempt = await work.fulfillment.attempt(claim)
    return None if attempt is None else (order, fulfillment, attempt)


class FulfillmentService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        cipher: InventoryCipher,
        sender: DeliverySender,
        policy: DeliveryPolicy,
    ) -> None:
        self.uow, self.cipher, self.sender, self.policy = uow, cipher, sender, policy

    async def claim(self) -> DeliveryClaim | None:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            fulfillment = await work.fulfillment.claimable(await work.orders.now())
            if fulfillment is None:
                return None
            now = await work.orders.now()
            attempt = DeliveryAttempt(
                uuid4(),
                fulfillment.work.id,
                DeliveryState.CLAIMED,
                now,
                now + timedelta(seconds=self.policy.claim_seconds),
            )
            await work.fulfillment.start_claim(fulfillment, attempt)
            await work.commit()
            return DeliveryClaim(fulfillment.work.id, fulfillment.work.order_id, attempt.id)

    async def prepare(self, claim: DeliveryClaim) -> DeliveryRequest | None:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            loaded = await locked_attempt(work, claim)
            if loaded is None:
                return None
            order, fulfillment, attempt = loaded
            now = await work.orders.now()
            if (
                fulfillment.active_attempt_id != claim.attempt_id
                or fulfillment.state != DeliveryState.CLAIMED
                or attempt.state != DeliveryState.CLAIMED
                or attempt.lease_expires_at <= now
            ):
                return None
            if fulfillment.hold:
                await work.fulfillment.abandon(claim, now, DeliveryCode.HELD)
                await work.commit()
                return None
            committed = fulfillment.work
            allowed = (
                fulfillment.send_count < self.policy.max_attempts
                and order.status in {OrderStatus.PAID, OrderStatus.FULFILLING}
                and order.attention_reason is None
                and order.buyer_id == committed.buyer_id
                and order.item_id == committed.order_item_id
                and order.unit_id == committed.inventory_unit_id
                and order.product_type == committed.product_type
                and await work.payments.primary(order.id) == committed.payment_id
            )
            encrypted = await work.fulfillment.entitlement(fulfillment) if allowed else None
            code = (
                DeliveryCode.RETRY_EXHAUSTED
                if fulfillment.send_count >= self.policy.max_attempts
                else DeliveryCode.INVALID_ENTITLEMENT
            )
            if encrypted is not None:
                try:
                    payload = self.cipher.open(encrypted.context, encrypted.sealed)
                    now = await work.orders.now()
                    if attempt.lease_expires_at <= now:
                        return None
                    deadline = now + timedelta(seconds=self.policy.send_seconds)
                    request = DeliveryRequest(
                        committed.id,
                        claim.attempt_id,
                        committed.destination_telegram_id,
                        encrypted.context,
                        payload,
                        deadline,
                        self.policy.timeout_seconds,
                    )
                except (InventoryCryptoError, InvalidInput):
                    code = DeliveryCode.CRYPTO
                else:
                    await work.fulfillment.start_send(claim, now, deadline)
                    await work.fulfillment.set_order_state(order.id, OrderStatus.FULFILLING)
                    await work.orders.audit(order, "delivery.sending")
                    await work.commit()
                    return request
            result = DeliveryResult(DeliveryState.FAILED, code=code)
            await work.fulfillment.finish(claim, result, DeliveryState.FAILED, now, now, code)
            if order.status in {OrderStatus.PAID, OrderStatus.FULFILLING}:
                await work.fulfillment.set_order_state(order.id, OrderStatus.FAILED)
            await work.orders.audit(order, "delivery.failed")
            await work.commit()
            logger.warning("delivery_attention work_id=%s code=%s", claim.work_id, code)
            return None

    async def complete(self, claim: DeliveryClaim, result: DeliveryResult) -> bool:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            loaded = await locked_attempt(work, claim)
            if loaded is None:
                return False
            order, fulfillment, attempt = loaded
            await work.fulfillment.observe(claim, result)
            if (
                fulfillment.active_attempt_id != claim.attempt_id
                or fulfillment.state != DeliveryState.SENDING
                or attempt.state != DeliveryState.SENDING
            ):
                await work.commit()
                return False
            now = await work.orders.now()
            state, code, due_at = result.state, result.code, now
            if attempt.lease_expires_at <= now:
                state, code = DeliveryState.UNCERTAIN, DeliveryCode.LEASE_EXPIRED
            elif state == DeliveryState.RETRYABLE:
                if fulfillment.send_count >= self.policy.max_attempts:
                    state, code = DeliveryState.FAILED, DeliveryCode.RETRY_EXHAUSTED
                else:
                    backoff = min(
                        self.policy.retry_cap_seconds,
                        self.policy.retry_base_seconds * 2 ** (fulfillment.send_count - 1)
                        + uniform(0, 1),
                    )
                    due_at = now + timedelta(seconds=max(result.retry_after, backoff))
                    state = DeliveryState.RETRY_WAIT
            await work.fulfillment.finish(claim, result, state, now, due_at, code)
            if state in {DeliveryState.SENT, DeliveryState.FAILED}:
                await work.fulfillment.set_order_state(
                    order.id,
                    OrderStatus.DELIVERED if state == DeliveryState.SENT else OrderStatus.FAILED,
                )
            await work.orders.audit(order, f"delivery.{state.value.lower()}")
            await work.commit()
            if state in {DeliveryState.UNCERTAIN, DeliveryState.FAILED}:
                logger.warning("delivery_attention work_id=%s code=%s", claim.work_id, code)
            return True

    async def recover(self, limit: int = 100) -> int:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Invalid recovery batch")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            claims = await work.fulfillment.expired(limit, await work.orders.now())
            count = 0
            attention = []
            for claim in claims:
                loaded = await locked_attempt(work, claim)
                if loaded is None:
                    continue
                order, fulfillment, attempt = loaded
                now = await work.orders.now()
                if (
                    fulfillment.active_attempt_id != attempt.id
                    or attempt.lease_expires_at > now
                    or fulfillment.state not in {DeliveryState.CLAIMED, DeliveryState.SENDING}
                ):
                    continue
                if fulfillment.state == DeliveryState.CLAIMED:
                    await work.fulfillment.abandon(claim, now, DeliveryCode.LEASE_EXPIRED)
                else:
                    result = DeliveryResult(
                        DeliveryState.UNCERTAIN, code=DeliveryCode.LEASE_EXPIRED
                    )
                    await work.fulfillment.finish(
                        claim, result, DeliveryState.UNCERTAIN, now, now, result.code
                    )
                    await work.orders.audit(order, "delivery.uncertain")
                    attention.append(claim.work_id)
                count += 1
            await work.commit()
            for work_id in attention:
                logger.warning(
                    "delivery_attention work_id=%s code=%s", work_id, DeliveryCode.LEASE_EXPIRED
                )
            return count

    async def run_once(self) -> bool:
        claim = await self.claim()
        if claim is None:
            return False
        request = await self.prepare(claim)
        if request is not None:
            result = await self.sender.send(request)
            await self.complete(claim, result)
        return True
