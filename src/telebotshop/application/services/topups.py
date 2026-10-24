from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.application.services.orders import validate_uuid
from telebotshop.application.services.wallet import wallet_operator
from telebotshop.domain.errors import AccessDenied, InvalidAction, InvalidInput
from telebotshop.domain.models.identity import validate_telegram_id
from telebotshop.domain.models.payments import validate_receipt_text
from telebotshop.domain.models.topups import (
    BankAccount,
    Settlement,
    SettlementResult,
    TopUpIntent,
    TopUpState,
)
from telebotshop.domain.models.wallet import JournalKind, WalletPermission, credits, quote_credits


def settlement_problem(intent, event, *, manual=False):
    if intent is None:
        return "NO_UNIQUE_INTENT"
    if intent.state in {TopUpState.CREDITED, TopUpState.REJECTED}:
        return "INTENT_ALREADY_CLOSED"
    if (intent.rail == "BANK_TRANSFER") != (event.provider == "SEPAY"):
        return "PROVIDER_RAIL_MISMATCH"
    if (intent.recipient, intent.asset, intent.network, intent.token_contract) != (
        event.recipient,
        event.asset,
        event.network,
        event.token_contract,
    ):
        return "DESTINATION_ASSET_MISMATCH"
    if event.amount != intent.amount or event.direction != "in":
        return "AMOUNT_DIRECTION_MISMATCH"
    if not intent.created_at - timedelta(seconds=2) <= event.occurred_at <= intent.expires_at:
        return "EXPIRED_OR_EARLY_SETTLEMENT"
    if not manual and intent.rail == "BANK_TRANSFER" and intent.reference != event.reference:
        return "REFERENCE_MISMATCH"
    return None


async def credit_topup(work, event_id, event, intent, actor_id=None, *, manual=False):
    problem = settlement_problem(intent, event, manual=manual)
    if problem is not None:
        await work.topups.finish(event_id, intent, TopUpState.REVIEW_REQUIRED, problem)
        return SettlementResult(
            event_id, None if intent is None else intent.id, TopUpState.REVIEW_REQUIRED, problem
        )
    minimum = event.required_confirmations
    if intent.route_id is not None:
        route = await work.topups.route(intent.buyer_id)
        minimum = max(minimum, route["minimum_confirmations"])
    if not event.confirmed or event.confirmations < minimum or not event.token_verified:
        await work.topups.finish(
            event_id, intent, TopUpState.PENDING_CONFIRMATION, "FINALITY_OR_TOKEN_UNVERIFIED"
        )
        return SettlementResult(
            event_id, intent.id, TopUpState.PENDING_CONFIRMATION, "FINALITY_OR_TOKEN_UNVERIFIED"
        )
    entry = await work.wallet.post(
        f"topup:{intent.id}",
        JournalKind.TOPUP,
        intent.id,
        intent.buyer_id,
        intent.credit_amount,
        actor_id,
        "Verified top-up" if not manual else "Manual verified top-up",
        event.digest(),
    )
    await work.topups.finish(event_id, intent, TopUpState.CREDITED, None, entry.id)
    await work.wallet.audit(actor_id, intent.id, "wallet.topup", entry.id)
    return SettlementResult(event_id, intent.id, TopUpState.CREDITED)


class TopUpService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        *,
        enabled: bool,
        bank: BankAccount | None = None,
        sandbox_rate: Decimal | None = None,
        bank_enabled: bool = True,
        usdt_enabled: bool = True,
    ):
        self.uow, self.enabled, self.bank, self.sandbox_rate = uow, enabled, bank, sandbox_rate
        self.bank_enabled, self.usdt_enabled = bank_enabled, usdt_enabled

    def available(self):
        if self.enabled is not True:
            raise InvalidAction("Sandbox top-up unavailable")

    async def create_bank(self, actor_id: int, amount: int, action_id: UUID) -> TopUpIntent:
        credits(amount)
        if self.bank is None or self.bank_enabled is not True:
            raise InvalidAction("Sandbox bank account unavailable")
        return await self._create(actor_id, Decimal(amount), action_id, "BANK_TRANSFER")

    async def create_usdt(self, actor_id: int, amount: Decimal, action_id: UUID) -> TopUpIntent:
        if self.sandbox_rate is None or self.usdt_enabled is not True:
            raise InvalidAction("Configured sandbox rate unavailable")
        quote_credits(amount, self.sandbox_rate)
        if amount.as_tuple().exponent < -18 or amount >= Decimal("1e15"):
            raise InvalidInput("Unsupported USDT precision")
        return await self._create(actor_id, amount, action_id, "USDT_BINANCE")

    async def _create(self, actor_id, amount, action_id, rail):
        self.available()
        validate_telegram_id(actor_id)
        validate_uuid(action_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            buyer = await work.orders.lock_buyer(actor_id)
            if buyer is None:
                raise AccessDenied("Wallet unavailable")
            existing = await work.topups.by_action(buyer, action_id)
            if existing is not None:
                if existing.rail != rail or existing.amount != amount:
                    raise InvalidAction("Top-up action conflicts")
                return existing
            route = None
            now = await work.orders.now()
            if rail == "USDT_BINANCE":
                route = await work.topups.route(buyer)
                if route is None:
                    raise InvalidAction("No verified exclusive sandbox deposit route")
                pending = await work.topups.active_route_intent(route["id"])
                if pending is not None:
                    if pending.expires_at > now or await work.topups.has_settlement(pending.id):
                        raise InvalidAction("Previous USDT quote requires settlement or review")
                    await work.topups.reject_unused_quote(pending.id)
                    await work.wallet.audit(buyer, pending.id, "topup.quote_expired", pending.id)
            rate = Decimal(1) if route is None else self.sandbox_rate
            intent_id = uuid4()
            intent = TopUpIntent(
                intent_id,
                buyer,
                action_id,
                rail,
                "TB" + intent_id.hex[:24].upper(),
                quote_credits(amount, rate),
                amount,
                "VND" if route is None else "USDT",
                self.bank.recipient if route is None else route["recipient"],
                "" if route is None else route["network"],
                "" if route is None else route["token_contract"],
                None if route is None else route["id"],
                rate,
                "VND_PAR" if route is None else "configured-sandbox-v1",
                now,
                now + timedelta(minutes=15 if route is None else 5),
            )
            await work.topups.save(intent)
            await work.wallet.audit(buyer, intent.id, "topup.created", intent.id)
            await work.commit()
            return intent

    async def set_sandbox_route(
        self, actor_id: int, customer_id: int, network: str, token: str, recipient: str
    ):
        self.available()
        for value in [network, token, recipient]:
            validate_receipt_text(value, 200)
        validate_telegram_id(customer_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.ADJUST)
            buyer = await work.orders.lock_buyer(customer_id)
            if buyer is None:
                raise AccessDenied("Customer unavailable")
            await work.topups.set_route(buyer, network, token, recipient)
            await work.wallet.audit(actor.user_id, buyer, "topup.sandbox_route", uuid4())
            await work.commit()

    async def ingest(self, event: Settlement) -> SettlementResult:
        self.available()
        if not isinstance(event, Settlement):
            raise InvalidInput("Verified settlement required")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            event_id, conflict, event = await work.topups.observe(event)
            if conflict:
                await work.wallet.audit(None, event_id, "topup.conflict", uuid4())
                await work.commit()
                return SettlementResult(event_id, None, TopUpState.REVIEW_REQUIRED, "CONFLICT")
            existing = await work.topups.event_result(event_id)
            if existing.state in {TopUpState.CREDITED, TopUpState.REJECTED}:
                await work.commit()
                return existing
            intent = await work.topups.match(event)
            result = await credit_topup(work, event_id, event, intent)
            await work.commit()
            return result

    async def history(self, actor_id: int, offset: int = 0):
        validate_telegram_id(actor_id)
        if type(offset) is not int or not 0 <= offset <= 10000:
            raise InvalidInput("Invalid top-up history page")
        async with self.uow() as work:
            user = await work.identity.get_profile(actor_id)
            if user is None:
                raise AccessDenied("Wallet unavailable")
            return await work.topups.own(user.id, offset)
