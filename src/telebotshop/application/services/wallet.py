from hashlib import sha256
from uuid import UUID, uuid4

from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.application.services.orders import validate_uuid
from telebotshop.domain.errors import AccessDenied, InvalidInput
from telebotshop.domain.models.identity import Role, validate_telegram_id
from telebotshop.domain.models.payments import validate_receipt_text
from telebotshop.domain.models.wallet import (
    JournalKind,
    LedgerEntry,
    LedgerReconciliation,
    WalletBalance,
    WalletPermission,
    credits,
)


async def wallet_operator(work: UnitOfWork, actor_id: int, permission: WalletPermission):
    validate_telegram_id(actor_id)
    await work.access.lock()
    actor = await work.access.get(actor_id)
    if actor is None or (
        actor.role != Role.OWNER
        and (
            actor.role != Role.ADMIN or not await work.wallet.permission(actor.user_id, permission)
        )
    ):
        raise AccessDenied("Wallet operation unavailable")
    return actor


class WalletLedgerService:
    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self.uow = uow

    async def balance(self, actor_id: int) -> WalletBalance:
        validate_telegram_id(actor_id)
        async with self.uow() as work:
            user = await work.identity.get_profile(actor_id)
            if user is None:
                raise AccessDenied("Wallet unavailable")
            return WalletBalance(await work.wallet.balance(user.id))

    async def history(self, actor_id: int, offset: int = 0) -> tuple[LedgerEntry, ...]:
        validate_telegram_id(actor_id)
        if type(offset) is not int or not 0 <= offset <= 10000:
            raise InvalidInput("Invalid history page")
        async with self.uow() as work:
            user = await work.identity.get_profile(actor_id)
            if user is None:
                raise AccessDenied("Wallet unavailable")
            return await work.wallet.history(user.id, offset)

    async def adjust(
        self,
        actor_id: int,
        customer_id: int,
        delta: int,
        action_id: UUID,
        reason: str,
        source: str,
    ) -> LedgerEntry:
        validate_telegram_id(customer_id)
        validate_uuid(action_id)
        if type(delta) is not int:
            raise InvalidInput("Invalid adjustment")
        credits(abs(delta))
        validate_receipt_text(reason, 300)
        validate_receipt_text(source, 200)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.ADJUST)
            customer = await work.identity.get_profile(customer_id)
            if customer is None:
                raise AccessDenied("Wallet unavailable")
            entry = await work.wallet.post(
                f"adjust:{action_id}",
                JournalKind.ADJUSTMENT,
                customer.id,
                customer.id,
                delta,
                actor.user_id,
                reason,
                sha256(source.encode()).digest(),
            )
            await work.wallet.audit(actor.user_id, customer.id, "wallet.adjustment", entry.id)
            await work.commit()
            return entry

    async def reconcile(self, actor_id: int) -> LedgerReconciliation:
        async with self.uow() as work:
            await wallet_operator(work, actor_id, WalletPermission.AUDIT)
            return await work.wallet.reconcile()

    async def operator_history(self, actor_id: int, customer_id: int):
        validate_telegram_id(customer_id)
        async with self.uow() as work:
            await wallet_operator(work, actor_id, WalletPermission.AUDIT)
            user = await work.identity.get_profile(customer_id)
            if user is None:
                raise AccessDenied("Customer unavailable")
            return WalletBalance(await work.wallet.balance(user.id)), await work.wallet.history(
                user.id, 0
            )

    async def grant_permission(
        self,
        actor_id: int,
        customer_id: int,
        permission: WalletPermission,
        allowed: bool,
        reason: str,
    ):
        validate_telegram_id(customer_id)
        if not isinstance(permission, WalletPermission) or type(allowed) is not bool:
            raise InvalidInput("Invalid financial delegation")
        validate_receipt_text(reason, 300)
        async with self.uow() as work:
            await work.access.lock(exclusive=True)
            actor = await wallet_operator(work, actor_id, WalletPermission.ADJUST)
            target = await work.access.get(customer_id)
            if actor.role != Role.OWNER or target is None or target.role != Role.ADMIN:
                raise AccessDenied("Owner and registered admin required")
            await work.wallet.grant(target.user_id, permission, allowed)
            await work.wallet.audit(
                actor.user_id,
                target.user_id,
                "wallet.permission",
                uuid4(),
                {"permission": permission.value, "allowed": allowed, "reason": reason},
            )
            await work.commit()
