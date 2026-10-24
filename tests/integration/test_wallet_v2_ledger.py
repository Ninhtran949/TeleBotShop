import asyncio
from contextlib import asynccontextmanager
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.wallet import WalletLedgerService
from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.identity import CustomerIdentity
from telebotshop.domain.models.wallet import WalletPermission
from telebotshop.infrastructure.database.uow import unit_of_work


async def wallet_fixture(engine):
    factory = partial(unit_of_work, engine)
    identity = IdentityService(factory)
    await identity.bootstrap_owner(9001)
    await identity.get_or_create_customer(CustomerIdentity(101, None, "FAKE wallet buyer"))
    return WalletLedgerService(factory)


@pytest.mark.asyncio
async def test_adjustment_is_balanced_idempotent_and_audited(migrated_database):
    wallet = await wallet_fixture(migrated_database)
    action = uuid4()
    entry = await wallet.adjust(9001, 101, 1000, action, "FAKE sandbox allocation", "FAKE-source-1")
    assert (
        await wallet.adjust(9001, 101, 1000, action, "FAKE sandbox allocation", "FAKE-source-1")
        == entry
    )
    assert (await wallet.balance(101)).available == 1000
    assert len(await wallet.history(101)) == 1
    with pytest.raises(InvalidAction):
        await wallet.adjust(9001, 101, 1001, action, "FAKE sandbox allocation", "FAKE-source-1")
    result = await wallet.reconcile(9001)
    assert result.balanced and result.mismatched_accounts == 0


@pytest.mark.asyncio
async def test_concurrent_debits_cannot_overspend(migrated_database):
    wallet = await wallet_fixture(migrated_database)
    await wallet.adjust(9001, 101, 1000, uuid4(), "FAKE funding", "FAKE-source")
    results = await asyncio.gather(
        *[
            wallet.adjust(9001, 101, -800, uuid4(), "FAKE controlled debit", "FAKE-source")
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, InvalidAction) for result in results) == 1
    assert (await wallet.balance(101)).available == 200
    assert (await wallet.reconcile(9001)).balanced


@pytest.mark.asyncio
async def test_wallet_admin_is_default_deny(migrated_database):
    wallet = await wallet_fixture(migrated_database)
    with pytest.raises(AccessDenied):
        await wallet.adjust(101, 101, 1000, uuid4(), "FAKE", "FAKE-source")
    with pytest.raises(AccessDenied):
        await wallet.reconcile(101)
    assert (await wallet.balance(101)).available == 0


@pytest.mark.asyncio
async def test_financial_grant_is_explicit_audited_and_current(migrated_database):
    from tests.refunds import refund_staff

    wallet = await wallet_fixture(migrated_database)
    await refund_staff(migrated_database, 202)
    with pytest.raises(AccessDenied):
        await wallet.adjust(202, 101, 1000, uuid4(), "FAKE", "FAKE-source")
    await wallet.grant_permission(
        9001, 202, WalletPermission.ADJUST, True, "FAKE approved delegation"
    )
    await wallet.adjust(202, 101, 1000, uuid4(), "FAKE allocation", "FAKE-source")
    await wallet.grant_permission(
        9001, 202, WalletPermission.ADJUST, False, "FAKE revoke delegation"
    )
    with pytest.raises(AccessDenied):
        await wallet.adjust(202, 101, 1000, uuid4(), "FAKE", "FAKE-source")
    async with migrated_database.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT changes FROM audit_logs WHERE action='wallet.permission' "
                        "ORDER BY created_at"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [r["allowed"] for r in rows] == [True, False]
        assert all(r["permission"] == WalletPermission.ADJUST.value and r["reason"] for r in rows)


@pytest.mark.asyncio
async def test_wallet_revoke_waits_for_authorized_adjustment_transaction(migrated_database):
    from tests.refunds import refund_staff

    engine = migrated_database
    wallet = await wallet_fixture(engine)
    await refund_staff(engine, 202)
    await wallet.grant_permission(9001, 202, WalletPermission.ADJUST, True, "FAKE delegation")
    authorized, release = asyncio.Event(), asyncio.Event()
    pids = []

    @asynccontextmanager
    async def paused_adjustment():
        async with unit_of_work(engine) as work:
            pids.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
            post = work.wallet.post

            async def held_post(*args, **kwargs):
                authorized.set()
                await release.wait()
                return await post(*args, **kwargs)

            work.wallet.post = held_post
            yield work

    @asynccontextmanager
    async def short_revoke():
        async with unit_of_work(engine) as work:
            pids.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
            await work.session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            yield work

    adjustment = asyncio.create_task(
        WalletLedgerService(paused_adjustment).adjust(
            202, 101, 321, uuid4(), "FAKE in-flight adjustment", "FAKE-source"
        )
    )
    try:
        await asyncio.wait_for(authorized.wait(), 3)
        with pytest.raises(DBAPIError) as blocked:
            await WalletLedgerService(short_revoke).grant_permission(
                9001, 202, WalletPermission.ADJUST, False, "FAKE revoke"
            )
        assert blocked.value.orig.sqlstate == "55P03"
        assert len(set(pids)) == 2
    finally:
        release.set()
        await asyncio.wait_for(adjustment, 3)

    assert (await wallet.balance(101)).available == 321
    await wallet.grant_permission(9001, 202, WalletPermission.ADJUST, False, "FAKE revoke")
    with pytest.raises(AccessDenied):
        await wallet.adjust(202, 101, 321, uuid4(), "FAKE after revoke", "FAKE-source")
    assert (await wallet.balance(101)).available == 321
    assert len(await wallet.history(101)) == 1
    assert (await wallet.reconcile(9001)).balanced


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE wallet_accounts SET balance=balance+1 WHERE user_id IS NOT NULL",
        "UPDATE wallet_postings SET delta=delta+1",
        "DELETE FROM wallet_journals",
        "TRUNCATE wallet_postings CASCADE",
    ],
)
async def test_database_rejects_ledger_tampering(migrated_database, mutation):
    wallet = await wallet_fixture(migrated_database)
    await wallet.adjust(9001, 101, 1000, uuid4(), "FAKE funding", "FAKE-source")
    with pytest.raises(DBAPIError):
        async with migrated_database.begin() as connection:
            await connection.execute(text(mutation))
    assert (await wallet.balance(101)).available == 1000
    assert (await wallet.reconcile(9001)).balanced


@pytest.mark.asyncio
async def test_database_rejects_journal_without_balancing_postings(migrated_database):
    await wallet_fixture(migrated_database)
    with pytest.raises(DBAPIError):
        async with migrated_database.begin() as connection:
            await connection.execute(
                text("""
                INSERT INTO wallet_journals
                  (id,operation_key,kind,subject_id,actor_id,reason,evidence)
                SELECT :id,'FAKE-unbalanced','ADJUSTMENT',id,id,'FAKE',decode(repeat('00',32),'hex')
                FROM users WHERE telegram_id=101
            """),
                {"id": uuid4()},
            )
