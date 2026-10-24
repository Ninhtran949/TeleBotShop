from datetime import timedelta
from hashlib import sha256
from uuid import UUID

from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.application.services.orders import validate_uuid
from telebotshop.application.services.topups import credit_topup
from telebotshop.application.services.wallet import wallet_operator
from telebotshop.domain.errors import AccessDenied, InvalidAction, InvalidInput
from telebotshop.domain.models.payments import validate_receipt_text
from telebotshop.domain.models.topups import SettlementResult, TopUpState
from telebotshop.domain.models.wallet import WalletPermission


class ManualPaymentReviewService:
    def __init__(self, uow: UnitOfWorkFactory):
        self.uow = uow

    async def queue(self, actor_id: int, offset: int = 0):
        if type(offset) is not int or not 0 <= offset <= 10000:
            raise InvalidInput("Invalid review page")
        async with self.uow() as work:
            await wallet_operator(work, actor_id, WalletPermission.REVIEW)
            return await work.topups.queue(offset)

    async def detail(self, actor_id: int, event_id: UUID):
        validate_uuid(event_id)
        async with self.uow() as work:
            await wallet_operator(work, actor_id, WalletPermission.REVIEW)
            event = await work.topups.evidence(event_id)
            if event is None:
                raise InvalidAction("Event missing or conflicting; escalation required")
            result = await work.topups.event_result(event_id)
            intent = None if result.intent_id is None else await work.topups.get(result.intent_id)
            return event, result, intent

    async def prepare(
        self,
        actor_id: int,
        event_id: UUID,
        intent_id: UUID | None,
        action_id: UUID,
        reason: str,
        source: str,
        *,
        decision: str = "APPROVE",
    ):
        if decision not in {"APPROVE", "REJECT", "ESCALATE"} or (
            decision == "APPROVE" and intent_id is None
        ):
            raise InvalidInput("Invalid review decision")
        for value in [event_id, action_id] + ([] if intent_id is None else [intent_id]):
            validate_uuid(value)
        validate_receipt_text(reason, 300)
        validate_receipt_text(source, 200)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.REVIEW)
            await work.topups.event_result(event_id)
            evidence = await work.topups.evidence(event_id)
            if (decision == "APPROVE" and evidence is None) or (
                intent_id is not None and await work.topups.get(intent_id) is None
            ):
                raise InvalidAction("Trusted settlement and intent required")
            await work.topups.save_review(
                action_id,
                actor.user_id,
                event_id,
                intent_id,
                reason,
                sha256(source.encode()).digest(),
                await work.orders.now() + timedelta(minutes=10),
                decision,
            )
            await work.wallet.audit(actor.user_id, event_id, "topup.review_prepared", action_id)
            await work.commit()
            return action_id

    async def confirm(self, actor_id: int, action_id: UUID):
        validate_uuid(action_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.REVIEW)
            # Read action without its row lock before taking canonical event -> intent locks.
            action = await work.topups.review(action_id)
            if action is None or action["actor_id"] != actor.user_id:
                raise AccessDenied("Review action unavailable")
            if action["applied"]:
                return await work.topups.event_result(action["event_id"])
            if action["expires_at"] <= await work.orders.now():
                raise InvalidAction("Review confirmation expired")
            event = await work.topups.evidence(action["event_id"])
            intent = (
                None if action["intent_id"] is None else await work.topups.get(action["intent_id"])
            )
            if event is None and action["decision"] == "APPROVE":
                raise InvalidAction("Conflicting settlement requires escalation")
            existing = await work.topups.event_result(action["event_id"])
            decision = action["decision"]
            if decision in {"REJECT", "ESCALATE"}:
                if decision == "REJECT" and existing.state == "CREDITED":
                    raise InvalidAction("Credited settlement requires a compensating operation")
                state = (
                    existing.state
                    if existing.state == "CREDITED"
                    else TopUpState.REJECTED
                    if decision == "REJECT"
                    else TopUpState.REVIEW_REQUIRED
                )
                reason = "OPERATOR_REJECTED" if decision == "REJECT" else "OPERATOR_ESCALATION"
                if existing.state == "CREDITED":
                    intent = await work.topups.get(existing.intent_id)
                await work.topups.finish(action["event_id"], intent, state, reason)
                result = SettlementResult(
                    action["event_id"], None if intent is None else intent.id, state, reason
                )
            elif existing.state == "CREDITED":
                result = existing
            elif existing.state == "REJECTED":
                raise InvalidAction("Rejected settlement cannot be automatically reopened")
            else:
                result = await credit_topup(
                    work, action["event_id"], event, intent, actor.user_id, manual=True
                )
            await work.topups.apply_review(action_id)
            await work.wallet.audit(
                actor.user_id, action["event_id"], "topup.review_applied", action_id
            )
            await work.commit()
            return result
