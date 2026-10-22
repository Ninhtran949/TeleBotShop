from datetime import timedelta
from uuid import UUID, uuid4, uuid5

from telebotshop.application.ports.inventory_crypto import InventoryCipher
from telebotshop.application.ports.refunds import RefundGateway
from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.application.services.access import require_permission
from telebotshop.application.services.orders import validate_uuid
from telebotshop.application.services.refund_receipts import apply_received_refund
from telebotshop.domain.errors import InvalidAction, InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.catalog import validate_page
from telebotshop.domain.models.fulfillment import (
    DeliveryClaim,
    DeliveryCode,
    DeliveryResult,
    DeliveryState,
)
from telebotshop.domain.models.payments import PaymentStatus
from telebotshop.domain.models.refunds import (
    RefundEvidence,
    RefundEvidenceContext,
    RefundIntent,
    RefundReceipt,
    RefundRequest,
    RefundResult,
    RefundState,
)


async def locked_payment(work: UnitOfWork, payment_id: UUID):
    payment = await work.refunds.payment(payment_id)
    if payment is None:
        raise InvalidAction("Payment unavailable")
    await work.payments.lock_charge(payment.receipt.charge_id)
    if payment.status == PaymentStatus.MATCHED:
        order = await work.orders.order(payment.order_id, for_update=True)
        snapshot = await work.refunds.snapshot(payment.id)
        if snapshot.work_id:
            fulfillment = await work.fulfillment.get(UUID(snapshot.work_id), for_update=True)
            now = await work.orders.now()
            if fulfillment.state == DeliveryState.SENDING:
                claim = DeliveryClaim(
                    fulfillment.work.id, payment.order_id, fulfillment.active_attempt_id
                )
                attempt = await work.fulfillment.attempt(claim)
                if attempt.lease_expires_at <= now:
                    result = DeliveryResult(
                        DeliveryState.UNCERTAIN, code=DeliveryCode.LEASE_EXPIRED
                    )
                    await work.fulfillment.finish(
                        claim, result, DeliveryState.UNCERTAIN, now, now, result.code
                    )
                    await work.orders.audit(order, "delivery.uncertain")
            resend = await work.history.active_resend(UUID(snapshot.work_id))
            if resend and resend.status == "SENDING" and resend.deadline <= now:
                await work.history.finish_resend(resend, "UNCERTAIN", now)
                await work.history.audit_resend(resend, payment.order_id, "resend.uncertain")
    return payment


def allowed_snapshot(snapshot, accept_uncertain):
    if snapshot.work_state == "SENDING" or snapshot.resend_state == "SENDING":
        raise InvalidAction("Delivery or resend is still in flight")
    if not accept_uncertain and "UNCERTAIN" in (snapshot.work_state, snapshot.resend_state):
        raise InvalidAction("Explicit uncertainty decision required")
    if snapshot.hold:
        raise InvalidAction("Delivery already held")


class RefundService:
    def __init__(self, uow: UnitOfWorkFactory, cipher: InventoryCipher, gateway: RefundGateway):
        self.uow, self.cipher, self.gateway = uow, cipher, gateway

    async def _authorize(self, work, actor_id):
        await work.orders.configure_timeouts()
        return await require_permission(work, actor_id, Permission.REFUND)

    async def _expire(self, work, intent):
        now = await work.orders.now()
        if intent and intent.status == RefundState.SENDING and intent.deadline <= now:
            await work.refunds.finish(intent, RefundState.UNCERTAIN, now)
            await work.refunds.audit(intent, "refund.uncertain")
            return await work.refunds.intent(intent.id)
        return intent

    async def queue(self, actor_id, page=0):
        validate_page(page)
        async with self.uow() as work:
            await self._authorize(work, actor_id)
            return await work.refunds.queue(page)

    async def detail(self, actor_id, payment_id):
        validate_uuid(payment_id)
        async with self.uow() as work:
            await self._authorize(work, actor_id)
            await locked_payment(work, payment_id)
            await self._expire(work, await work.refunds.active(payment_id))
            result = await work.refunds.view(payment_id)
            await work.commit()
            return result

    async def prepare(self, actor_id, payment_id, reference, *, accept_uncertain=False):
        validate_uuid(payment_id)
        evidence = RefundEvidence(reference)
        if type(accept_uncertain) is not bool:
            raise InvalidInput("Invalid uncertainty decision")
        async with self.uow() as work:
            actor = await self._authorize(work, actor_id)
            payment = await locked_payment(work, payment_id)
            active = await self._expire(work, await work.refunds.active(payment_id))
            now = await work.orders.now()
            if active:
                if active.status == RefundState.PENDING and active.expires_at <= now:
                    await work.refunds.finish(active, RefundState.CANCELLED, now)
                    await work.refunds.audit(active, "refund.expired")
                elif (
                    active.status == RefundState.PENDING
                    and active.actor_id == actor.user_id
                    and active.accept_uncertain == accept_uncertain
                ):
                    await work.commit()
                    return active
                else:
                    raise InvalidAction("Refund already active or completed")
            if (
                payment.receipt.currency != "XTR"
                or payment.receipt.amount <= 0
                or await work.refunds.payer(payment.id) is None
                or not (await work.refunds.view(payment.id)).verified
            ):
                raise InvalidAction("Verified received Stars charge required")
            snapshot = await work.refunds.snapshot(payment.id)
            allowed_snapshot(snapshot, accept_uncertain)
            await work.inventory.check_keys(self.cipher.proof())
            intent = RefundIntent(
                uuid4(),
                payment.id,
                actor.user_id,
                snapshot,
                accept_uncertain,
                now + timedelta(minutes=10),
            )
            await work.refunds.save(
                intent, self.cipher.seal(RefundEvidenceContext(payment.id, intent.id), evidence)
            )
            await work.refunds.audit(intent, "refund.prepared")
            await work.commit()
            return intent

    async def begin(self, actor_id, intent_id):
        validate_uuid(intent_id)
        async with self.uow() as work:
            actor = await self._authorize(work, actor_id)
            intent = await work.refunds.intent(intent_id)
            if intent is None or intent.actor_id != actor.user_id:
                raise InvalidAction("Refund confirmation unavailable")
            payment = await locked_payment(work, intent.payment_id)
            intent = await work.refunds.intent(intent_id, for_update=True)
            intent = await self._expire(work, intent)
            if intent.status != RefundState.PENDING:
                await work.commit()
                return intent
            now = await work.orders.now()
            snapshot = await work.refunds.snapshot(payment.id)
            if intent.expires_at <= now or intent.expected != snapshot:
                raise InvalidAction("Refund confirmation expired or changed")
            allowed_snapshot(snapshot, intent.accept_uncertain)
            evidence = self.cipher.open(
                RefundEvidenceContext(payment.id, intent.id), await work.refunds.evidence(intent.id)
            )
            if not isinstance(evidence, RefundEvidence):
                raise InvalidAction("Refund evidence unavailable")
            request = RefundRequest(
                intent.id,
                payment.receipt.charge_id,
                await work.refunds.payer(payment.id),
                now + timedelta(seconds=60),
            )
            await work.refunds.start(intent, now, request.deadline)
            await work.refunds.hold(intent)
            await work.refunds.audit(intent, "refund.sending")
            await work.commit()
            return request

    async def complete(self, intent_id, result: RefundResult):
        validate_uuid(intent_id)
        if not isinstance(result, RefundResult):
            raise InvalidInput("Invalid refund result")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            intent = await work.refunds.intent(intent_id)
            if intent is None:
                raise InvalidAction("Refund unavailable")
            payment = await locked_payment(work, intent.payment_id)
            intent = await work.refunds.intent(intent_id, for_update=True)
            intent = await self._expire(work, intent)
            await work.refunds.observe(intent, result)
            now = await work.orders.now()
            if intent.status in {RefundState.SENDING, RefundState.UNCERTAIN}:
                if result.state == RefundState.SUCCEEDED:
                    await work.refunds.finish(intent, RefundState.SUCCEEDED, now, "API")
                    await work.refunds.mark_refunded(payment)
                    await work.refunds.audit(intent, "refund.succeeded")
                elif intent.status == RefundState.SENDING:
                    await work.refunds.finish(intent, result.state, now)
                    if result.state == RefundState.FAILED:
                        await work.refunds.hold(intent, release=True)
                    await work.refunds.audit(intent, "refund." + result.state.value.lower())
            updated = await work.refunds.intent(intent_id)
            await work.commit()
            return updated

    async def confirm(self, actor_id, intent_id):
        prepared = await self.begin(actor_id, intent_id)
        if isinstance(prepared, RefundIntent):
            return prepared
        return await self.complete(intent_id, await self.gateway.refund(prepared))

    async def cancel(self, actor_id, intent_id):
        validate_uuid(intent_id)
        async with self.uow() as work:
            actor = await self._authorize(work, actor_id)
            intent = await work.refunds.intent(intent_id)
            if intent is None or intent.actor_id != actor.user_id:
                raise InvalidAction("Refund confirmation unavailable")
            await locked_payment(work, intent.payment_id)
            intent = await work.refunds.intent(intent_id, for_update=True)
            if intent.status == RefundState.PENDING:
                await work.refunds.finish(intent, RefundState.CANCELLED, await work.orders.now())
                await work.refunds.audit(intent, "refund.cancelled")
                await work.commit()
            elif intent.status != RefundState.CANCELLED:
                raise InvalidAction("Refund cannot be cancelled")

    async def verify(self, actor_id, payment_id, offset=0):
        validate_uuid(payment_id)
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise InvalidInput("Invalid transaction offset")
        async with self.uow() as work:
            await self._authorize(work, actor_id)
            if await work.refunds.payment(payment_id) is None:
                raise InvalidAction("Payment unavailable")
        transactions = await self.gateway.transactions(offset)
        async with self.uow() as work:
            actor = await self._authorize(work, actor_id)
            payment = await locked_payment(work, payment_id)
            receipt = payment.receipt
            match = next(
                (
                    e
                    for e in transactions
                    if not e.refunded
                    and e.charge_id == receipt.charge_id
                    and e.amount == receipt.amount
                    and e.payload == receipt.payload
                    and (receipt.payer_id is None or e.payer_id == receipt.payer_id)
                ),
                None,
            )
            if receipt.currency != "XTR" or match is None:
                return False
            await work.refunds.verify(payment.id, actor.user_id, match)
            await work.refunds.audit_operation(
                actor.user_id,
                payment.id,
                "payment.verified",
                uuid5(payment.id, "payment.verified:" + str(actor.user_id)),
            )
            await work.commit()
            return True

    async def record(self, receipt: RefundReceipt):
        if not isinstance(receipt, RefundReceipt) or receipt.source != "EVENT":
            raise InvalidInput("Invalid refund receipt")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            await work.payments.lock_charge(receipt.charge_id)
            await work.refunds.receipt(receipt)
            payment = await work.payments.by_charge(receipt.charge_id)
            result = None
            if payment:
                await locked_payment(work, payment.id)
                result = await apply_received_refund(work, payment)
            await work.commit()
            return result

    async def reconcile(self, actor_id, payment_id, offset=0):
        validate_uuid(payment_id)
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise InvalidInput("Invalid transaction offset")
        async with self.uow() as work:
            await self._authorize(work, actor_id)
            if await work.refunds.payment(payment_id) is None:
                raise InvalidAction("Payment unavailable")
        transactions = await self.gateway.transactions(offset)
        async with self.uow() as work:
            actor = await self._authorize(work, actor_id)
            payment = await locked_payment(work, payment_id)
            receipt = payment.receipt
            payer = await work.refunds.payer(payment.id)
            match = next(
                (
                    e
                    for e in transactions
                    if e.refunded
                    and e.charge_id == receipt.charge_id
                    and e.amount == receipt.amount
                    and e.payer_id == payer
                    and (e.payload is None or e.payload == receipt.payload)
                ),
                None,
            )
            if receipt.currency != "XTR" or match is None:
                return False
            await work.refunds.receipt(
                RefundReceipt(
                    receipt.charge_id,
                    "XTR",
                    receipt.amount,
                    receipt.payload,
                    receipt.provider_charge_id,
                    source="TRANSACTION",
                )
            )
            intent = await apply_received_refund(work, payment)
            await work.refunds.audit_operation(
                actor.user_id,
                payment.id,
                "refund.reconciled",
                uuid5(intent.id, "refund.reconciled:" + str(actor.user_id)),
                intent.id,
            )
            await work.commit()
            return True
