from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.access import AccessService, require_permission
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.admin import AdminAction
from telebotshop.domain.models.identity import CustomerIdentity, Role
from telebotshop.infrastructure.database.uow import SqlAlchemyUnitOfWork, unit_of_work


async def prepare_grant(database: AsyncEngine) -> tuple[AccessService, AdminAction]:
    factory = partial(unit_of_work, database)
    identity = IdentityService(factory)
    await identity.bootstrap_owner(9001)
    await identity.get_or_create_customer(CustomerIdentity(101, "fake_staff", "Fake staff"))
    service = AccessService(factory)
    action = await service.prepare_permission(9001, 101, Role.ADMIN, Permission.REFUND, True)
    return service, action


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_audit_or_commit_failure_rolls_back_grant_version_and_confirmation(
    migrated_database: AsyncEngine,
    deferred: bool,
) -> None:
    service, action = await prepare_grant(migrated_database)
    async with migrated_database.begin() as connection:
        await connection.execute(
            text("""
            CREATE FUNCTION fail_test_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'Synthetic audit failure' USING ERRCODE = '23514'; END;
            $$
        """)
        )
        trigger = (
            "CREATE CONSTRAINT TRIGGER fail_test_audit AFTER INSERT ON audit_logs "
            "DEFERRABLE INITIALLY DEFERRED"
            if deferred
            else "CREATE TRIGGER fail_test_audit BEFORE INSERT ON audit_logs"
        )
        await connection.execute(
            text(
                trigger + " FOR EACH ROW WHEN (NEW.action = 'permission.grant') "
                "EXECUTE FUNCTION fail_test_audit()"
            )
        )
    with pytest.raises(DBAPIError):
        await service.confirm(9001, action.id)
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM user_permissions")) == 0
        assert (
            await connection.scalar(text("SELECT count(*) FROM user_roles WHERE role = 'ADMIN'"))
            == 0
        )
        assert (
            await connection.scalar(
                text("SELECT access_version FROM users WHERE telegram_id = 101")
            )
            == 0
        )
        assert await connection.scalar(text("SELECT status FROM admin_actions")) == "pending"
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_logs SET action = 'tampered'",
        "DELETE FROM audit_logs",
        "TRUNCATE audit_logs",
    ],
)
async def test_audit_cannot_be_updated_deleted_or_truncated(
    migrated_database: AsyncEngine,
    statement: str,
) -> None:
    await prepare_grant(migrated_database)
    with pytest.raises(DBAPIError, match="append-only"):
        async with migrated_database.begin() as connection:
            await connection.execute(text(statement))
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT action FROM audit_logs")) == "owner.bootstrap"


@pytest.mark.asyncio
async def test_permission_check_holds_lock_until_use_case_transaction_ends(
    migrated_database: AsyncEngine,
) -> None:
    service, grant = await prepare_grant(migrated_database)
    await service.confirm(9001, grant.id)
    revoke = await service.prepare_permission(9001, 101, Role.ADMIN, Permission.REFUND, False)

    @asynccontextmanager
    async def short_lock_uow() -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with unit_of_work(migrated_database) as work:
            await work.session.execute(text("SET LOCAL lock_timeout = '50ms'"))
            yield work

    async with unit_of_work(migrated_database) as protected_work:
        await require_permission(protected_work, 101, Permission.REFUND)
        with pytest.raises(DBAPIError) as failure:
            await AccessService(short_lock_uow).confirm(9001, revoke.id)
        assert failure.value.orig.sqlstate == "55P03"
        await require_permission(protected_work, 101, Permission.REFUND)
        await protected_work.commit()
    assert await service.confirm(9001, revoke.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO user_roles SELECT id, 'OWNER' FROM users WHERE telegram_id = 101",
        "INSERT INTO user_permissions SELECT id, 'unknown.permission' "
        "FROM users WHERE telegram_id = 101",
        "INSERT INTO user_permissions SELECT id, 'payments.refund' "
        "FROM users WHERE telegram_id = 101 "
        "UNION ALL SELECT id, 'payments.refund' FROM users WHERE telegram_id = 101",
    ],
)
async def test_database_constraints_reject_second_owner_unknown_and_duplicate_grants(
    migrated_database: AsyncEngine,
    statement: str,
) -> None:
    await prepare_grant(migrated_database)
    with pytest.raises(IntegrityError):
        async with migrated_database.begin() as connection:
            await connection.execute(text(statement))
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM user_roles")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM user_permissions")) == 0


@pytest.mark.asyncio
async def test_even_current_owner_cannot_confirm_another_actors_action(
    migrated_database: AsyncEngine,
) -> None:
    service, action = await prepare_grant(migrated_database)
    async with migrated_database.begin() as connection:
        await connection.execute(
            text(
                "UPDATE admin_actions SET actor_id = (SELECT id FROM users WHERE telegram_id = 101)"
            )
        )
    with pytest.raises(InvalidAction):
        await service.confirm(9001, action.id)
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM user_permissions")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 1
