import json
from datetime import timedelta
from hashlib import sha256
from uuid import UUID

from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.application.services.orders import validate_uuid
from telebotshop.application.services.wallet import wallet_operator
from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.history import ResendState
from telebotshop.domain.models.orders import PaymentMethod
from telebotshop.domain.models.payments import validate_receipt_text
from telebotshop.domain.models.wallet import JournalKind, WalletPermission


async def refund_scope(work, order_id):
    order = await work.orders.order(order_id, for_update=True)
    if order is None or order.payment_method != PaymentMethod.WALLET_CREDIT:
        raise InvalidAction("Credit order unavailable")
    view = await work.history.detail(order_id, None)
    if view is None or view.work_id is None:
        raise InvalidAction("Credit entitlement unavailable")
    delivery = await work.fulfillment.get(view.work_id, for_update=True)
    resend = await work.history.active_resend(view.work_id)
    if (
        order.status not in {"PAID", "FULFILLING", "DELIVERED", "FAILED"}
        or delivery.hold
        or delivery.state in {"SENDING", "UNCERTAIN"}
        or (resend is not None and resend.status in {"SENDING", "UNCERTAIN"})
    ):
        raise InvalidAction("Delivery requires reconciliation before credit refund")
    snapshot = sha256(
        json.dumps(
            [
                order.status,
                delivery.state,
                delivery.send_count,
                str(delivery.active_attempt_id),
                None if resend is None else str(resend.id),
            ],
            separators=(",", ":"),
        ).encode()
    ).digest()
    return order, delivery, resend, snapshot


class WalletRefundService:
    def __init__(self, uow: UnitOfWorkFactory):
        self.uow = uow

    async def external_refund_history(self, actor_id: int, topup_id: UUID):
        validate_uuid(topup_id)
        async with self.uow() as work:
            await wallet_operator(work, actor_id, WalletPermission.REFUND)
            return await work.credit_orders.external_refunds(topup_id)

    async def record_external_refund(
        self,
        actor_id: int,
        topup_id: UUID,
        action_id: UUID,
        state: str,
        reason: str,
        source: str,
    ):
        for value in [topup_id, action_id]:
            validate_uuid(value)
        validate_receipt_text(reason, 300)
        validate_receipt_text(source, 200)
        if state not in {"REQUESTED", "UNCERTAIN", "CONFIRMED", "REJECTED"}:
            raise InvalidAction("Invalid external refund state")
        proof = sha256(source.encode()).digest()
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.REFUND)
            topup = await work.topups.get(topup_id)
            if topup is None or topup.state != "CREDITED":
                raise InvalidAction("Credited top-up required")
            records = await work.credit_orders.external_refunds(topup_id)
            existing = next((r for r in records if r["id"] == action_id), None)
            if existing is not None:
                if (
                    existing["actor_id"],
                    existing["state"],
                    existing["reason"],
                    existing["source_digest"],
                ) != (
                    actor.user_id,
                    state,
                    reason,
                    proof,
                ):
                    raise InvalidAction("External refund action conflicts")
                return action_id
            previous = None if not records else records[-1]["state"]
            if (previous is None and state != "REQUESTED") or (
                previous is not None
                and (previous in {"CONFIRMED", "REJECTED"} or state in {"REQUESTED", previous})
            ):
                raise InvalidAction("External refund transition unavailable")
            if (
                state == "CONFIRMED"
                and await work.wallet.by_operation("reversal:" + str(topup_id)) is None
            ):
                raise InvalidAction(
                    "Reverse the top-up credit before recording cash refund success"
                )
            await work.credit_orders.record_external_refund(
                action_id, topup_id, actor.user_id, state, reason, proof
            )
            await work.wallet.audit(
                actor.user_id,
                topup_id,
                "wallet.external_refund_record",
                action_id,
                {"state": state, "money_moved_by_app": False},
            )
            await work.commit()
            return action_id

    async def prepare(
        self, actor_id: int, order_id: UUID, action_id: UUID, reason: str, source: str
    ):
        validate_uuid(order_id)
        validate_uuid(action_id)
        validate_receipt_text(reason, 300)
        validate_receipt_text(source, 200)
        proof = sha256(source.encode()).digest()
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.REFUND)
            existing = await work.credit_orders.refund_action(action_id)
            if existing is not None:
                if (
                    existing["order_id"],
                    existing["actor_id"],
                    existing["reason"],
                    existing["source_digest"],
                ) != (order_id, actor.user_id, reason, proof):
                    raise InvalidAction("Credit refund action conflicts")
                return action_id
            order, delivery, resend, snapshot = await refund_scope(work, order_id)
            await work.credit_orders.save_refund(
                action_id,
                order_id,
                actor.user_id,
                reason,
                proof,
                snapshot,
                await work.orders.now() + timedelta(minutes=10),
            )
            await work.wallet.audit(actor.user_id, order_id, "wallet.refund_prepared", action_id)
            await work.commit()
            return action_id

    async def confirm(self, actor_id: int, action_id: UUID):
        validate_uuid(action_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.REFUND)
            action = await work.credit_orders.refund_action(action_id)
            if action is None or action["actor_id"] != actor.user_id:
                raise AccessDenied("Credit refund unavailable")
            await work.orders.order(action["order_id"], for_update=True)
            action = await work.credit_orders.refund_action(action_id, for_update=True)
            if action["journal_id"] is not None:
                return await work.wallet.by_operation("refund:" + str(action["order_id"]))
            order, delivery, resend, snapshot = await refund_scope(work, action["order_id"])
            action = await work.credit_orders.refund_action(action_id, for_update=True)
            if action["expires_at"] <= await work.orders.now() or action["expected"] != snapshot:
                raise InvalidAction("Credit refund confirmation stale or expired")
            entry = await work.wallet.post(
                "refund:" + str(order.id),
                JournalKind.REFUND,
                order.id,
                order.buyer_id,
                order.price_credits,
                actor.user_id,
                action["reason"],
                action["source_digest"],
            )
            if resend is not None:
                await work.history.finish_resend(
                    resend, ResendState.CANCELLED, await work.orders.now()
                )
            await work.credit_orders.apply_refund(action_id, entry.id, order.id, delivery.work.id)
            await work.wallet.audit(actor.user_id, order.id, "wallet.refund", entry.id)
            await work.commit()
            return entry

    async def reverse_topup(self, actor_id: int, topup_id: UUID, reason: str, source: str):
        validate_uuid(topup_id)
        validate_receipt_text(reason, 300)
        validate_receipt_text(source, 200)
        proof = sha256(source.encode()).digest()
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.REFUND)
            intent = await work.topups.get(topup_id)
            if intent is None or intent.state != "CREDITED":
                raise InvalidAction("Credited top-up required")
            entry = await work.wallet.post(
                "reversal:" + str(topup_id),
                JournalKind.REVERSAL,
                topup_id,
                intent.buyer_id,
                -intent.credit_amount,
                actor.user_id,
                reason,
                proof,
            )
            await work.credit_orders.reverse_topup(topup_id, entry.id, actor.user_id, reason, proof)
            await work.wallet.audit(actor.user_id, topup_id, "wallet.topup_reversal", entry.id)
            await work.commit()
            return entry
