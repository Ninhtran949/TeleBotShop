from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from telebotshop.application.ports.wallet import WalletRepository
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.wallet import JournalKind, LedgerEntry, LedgerReconciliation
from telebotshop.infrastructure.database.models.identity import audit_logs, user_roles
from telebotshop.infrastructure.database.models.wallet import (
    wallet_accounts as accounts,
)
from telebotshop.infrastructure.database.models.wallet import (
    wallet_journals as journals,
)
from telebotshop.infrastructure.database.models.wallet import (
    wallet_permissions as permissions,
)
from telebotshop.infrastructure.database.models.wallet import (
    wallet_postings as postings,
)


def entry_query():
    return (
        select(journals, accounts.c.user_id, postings.c.delta)
        .select_from(journals.join(postings).join(accounts))
        .where(accounts.c.user_id.is_not(None))
    )


def entry_from(row) -> LedgerEntry:
    return LedgerEntry(**{**row, "kind": JournalKind(row["kind"])})


class SqlWalletRepository(WalletRepository):
    def __init__(self, session) -> None:
        self.session = session

    async def ensure_account(self, user_id):
        await self.session.execute(
            pg_insert(accounts).values(id=uuid4(), user_id=user_id).on_conflict_do_nothing()
        )
        return await self.session.scalar(select(accounts.c.id).where(accounts.c.user_id == user_id))

    async def permission(self, user_id, permission):
        return (
            await self.session.scalar(
                select(permissions.c.user_id).where(
                    permissions.c.user_id == user_id,
                    permissions.c.permission == permission,
                )
            )
            is not None
        )

    async def grant(self, user_id, permission, allowed):
        if allowed:
            await self.session.execute(
                pg_insert(permissions)
                .values(user_id=user_id, permission=permission)
                .on_conflict_do_nothing()
            )
        else:
            await self.session.execute(
                delete(permissions).where(
                    permissions.c.user_id == user_id, permissions.c.permission == permission
                )
            )

    async def balance(self, user_id):
        return (
            await self.session.scalar(
                select(accounts.c.balance).where(accounts.c.user_id == user_id)
            )
            or 0
        )

    async def by_operation(self, key):
        row = (
            (await self.session.execute(entry_query().where(journals.c.operation_key == key)))
            .mappings()
            .one_or_none()
        )
        return None if row is None else entry_from(dict(row))

    async def post(self, key, kind, subject_id, user_id, delta, actor_id, reason, evidence):
        customer_account = await self.ensure_account(user_id)
        system = (
            "SALES"
            if kind in {JournalKind.PURCHASE, JournalKind.REFUND}
            else "CLEARING"
            if kind in {JournalKind.TOPUP, JournalKind.REVERSAL}
            else "ADJUSTMENT"
        )
        system_account = uuid5(NAMESPACE_URL, "telebotshop.wallet:" + system)
        await self.session.execute(
            pg_insert(accounts)
            .values(id=system_account, system_code=system)
            .on_conflict_do_nothing()
        )
        locked = (
            (
                await self.session.execute(
                    select(accounts)
                    .where(accounts.c.id.in_([customer_account, system_account]))
                    .order_by(accounts.c.id)
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        existing = await self.by_operation(key)
        if existing is not None:
            if (
                existing.kind,
                existing.subject_id,
                existing.user_id,
                existing.delta,
                existing.actor_id,
                existing.reason,
                existing.evidence,
            ) != (kind, subject_id, user_id, delta, actor_id, reason, evidence):
                raise InvalidAction("Ledger operation conflicts with existing evidence")
            return existing
        balance = next(row["balance"] for row in locked if row["id"] == customer_account)
        if balance + delta < 0:
            raise InvalidAction("Insufficient credit balance")
        journal_id = uuid4()
        await self.session.execute(
            insert(journals).values(
                id=journal_id,
                operation_key=key,
                kind=kind,
                subject_id=subject_id,
                actor_id=actor_id,
                reason=reason,
                evidence=evidence,
            )
        )
        await self.session.execute(
            insert(postings),
            [
                {"journal_id": journal_id, "account_id": customer_account, "delta": delta},
                {"journal_id": journal_id, "account_id": system_account, "delta": -delta},
            ],
        )
        return await self.by_operation(key)

    async def history(self, user_id, offset):
        rows = (
            await self.session.execute(
                entry_query()
                .where(accounts.c.user_id == user_id)
                .order_by(journals.c.created_at.desc(), journals.c.id.desc())
                .limit(20)
                .offset(offset)
            )
        ).mappings()
        return tuple(entry_from(dict(row)) for row in rows)

    async def reconcile(self):
        row = (
            await self.session.execute(
                text("""
            SELECT (SELECT coalesce(sum(delta),0)=0 FROM wallet_postings) AS balanced,
            (SELECT count(*) FROM wallet_accounts a WHERE a.balance <>
              coalesce((SELECT sum(p.delta) FROM wallet_postings p
                WHERE p.account_id=a.id),0)) AS mismatched
        """)
            )
        ).one()
        return LedgerReconciliation(row.balanced and row.mismatched == 0, row.mismatched)

    async def audit(self, actor_id, target_id, event, correlation_id, changes=None):
        system = actor_id is None
        if system:
            actor_id = await self.session.scalar(
                select(user_roles.c.user_id).where(user_roles.c.role == "OWNER")
            )
        await self.session.execute(
            pg_insert(audit_logs)
            .values(
                id=uuid5(correlation_id, event),
                actor_id=actor_id,
                action=event,
                target_id=target_id,
                correlation_id=uuid5(correlation_id, event),
                changes={"system": system, **(changes or {})},
            )
            .on_conflict_do_nothing()
        )
