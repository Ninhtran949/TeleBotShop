import asyncio
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.errors import AccessDenied, InvalidAction, InvalidInput
from telebotshop.domain.models.access import Permission, ResourceScope
from telebotshop.domain.models.identity import CustomerIdentity, Role
from telebotshop.infrastructure.database.uow import unit_of_work


async def setup_access(database: AsyncEngine) -> AccessService:
    factory = partial(unit_of_work, database)
    identity = IdentityService(factory)
    await identity.bootstrap_owner(9001)
    for telegram_id in (101, 102):
        await identity.get_or_create_customer(CustomerIdentity(telegram_id, None, "Fake"))
    return AccessService(factory)


@pytest.mark.asyncio
async def test_default_deny_direct_calls_and_current_grant_revoke(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_access(migrated_database)
    for actor in (101, 999):
        with pytest.raises(AccessDenied):
            await service.admin_access(actor)
        with pytest.raises(AccessDenied):
            await service.prepare_permission(actor, 102, Role.ADMIN, Permission.REFUND, True)
    grant = await service.prepare_permission(9001, 101, Role.ADMIN, Permission.REFUND, True)
    with pytest.raises(AccessDenied):
        await service.authorize(101, Permission.REFUND)
    assert await service.confirm(9001, grant.id)
    assert (await service.authorize(101, Permission.REFUND)).role == Role.ADMIN
    with pytest.raises(AccessDenied):
        await service.prepare_permission(101, 102, Role.ADMIN, Permission.REFUND, True)
    revoke = await service.prepare_permission(9001, 101, Role.ADMIN, Permission.REFUND, False)
    assert await service.confirm(9001, revoke.id)
    assert not await service.confirm(9001, grant.id)
    with pytest.raises(AccessDenied):
        await service.authorize(101, Permission.REFUND)
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM user_permissions")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 3


@pytest.mark.asyncio
async def test_support_scope_and_role_ceiling_are_enforced_from_direct_calls(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_access(migrated_database)
    action = await service.prepare_permission(
        9001, 101, Role.SUPPORT, Permission.MANAGE_TICKETS, True
    )
    await service.confirm(9001, action.id)
    for scope in (None, ResourceScope(assigned_staff_id=102)):
        with pytest.raises(AccessDenied):
            await service.authorize(101, Permission.MANAGE_TICKETS, scope)
    await service.authorize(101, Permission.MANAGE_TICKETS, ResourceScope(assigned_staff_id=101))
    with pytest.raises(InvalidInput):
        await service.prepare_permission(9001, 101, Role.SUPPORT, Permission.REFUND, True)
    with pytest.raises(InvalidInput):
        await service.prepare_permission(9001, 101, Role.ADMIN, Permission.MANAGE_ACCESS, True)
    with pytest.raises(InvalidAction):
        await service.prepare_permission(9001, 9001, Role.ADMIN, Permission.REFUND, True)


@pytest.mark.asyncio
async def test_confirm_is_actor_bound_concurrent_and_replay_safe(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_access(migrated_database)
    action = await service.prepare_permission(
        9001, 101, Role.ADMIN, Permission.MANAGE_CATALOG, True
    )
    with pytest.raises(AccessDenied):
        await service.confirm(102, action.id)
    results = await asyncio.gather(
        service.confirm(9001, action.id), service.confirm(9001, action.id)
    )
    assert sorted(results) == [False, True]
    assert not await service.confirm(9001, action.id)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, uuid4())
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM user_permissions")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 2


@pytest.mark.asyncio
async def test_competing_grants_require_fresh_confirmation(migrated_database: AsyncEngine) -> None:
    service = await setup_access(migrated_database)
    catalog, stock = await asyncio.gather(
        service.prepare_permission(9001, 101, Role.ADMIN, Permission.MANAGE_CATALOG, True),
        service.prepare_permission(9001, 101, Role.ADMIN, Permission.MANAGE_INVENTORY, True),
    )
    results = await asyncio.gather(
        service.confirm(9001, catalog.id), service.confirm(9001, stock.id), return_exceptions=True
    )
    assert sum(result is True for result in results) == 1
    assert sum(isinstance(result, InvalidAction) for result in results) == 1
    access = await service.admin_access(101)
    assert len(access.permissions) == 1
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 2


@pytest.mark.asyncio
async def test_expired_cancelled_and_revoked_owner_confirmations_do_not_mutate(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_access(migrated_database)
    expired = await service.prepare_permission(9001, 101, Role.ADMIN, Permission.REFUND, True)
    async with migrated_database.begin() as connection:
        await connection.execute(
            text("UPDATE admin_actions SET expires_at = now() - interval '1 second'")
        )
    with pytest.raises(InvalidAction):
        await service.confirm(9001, expired.id)
    cancelled = await service.prepare_permission(9001, 101, Role.ADMIN, Permission.REFUND, True)
    await service.cancel(9001, cancelled.id)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, cancelled.id)
    pending = await service.prepare_permission(9001, 101, Role.ADMIN, Permission.REFUND, True)
    async with migrated_database.begin() as connection:
        await connection.execute(text("DELETE FROM user_roles WHERE role = 'OWNER'"))
    with pytest.raises(AccessDenied):
        await service.confirm(9001, pending.id)
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM user_permissions")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 1


@pytest.mark.asyncio
async def test_staff_role_change_requires_revoking_old_grants_first(
    migrated_database: AsyncEngine,
) -> None:
    service = await setup_access(migrated_database)
    support = await service.prepare_permission(
        9001, 101, Role.SUPPORT, Permission.MANAGE_TICKETS, True
    )
    await service.confirm(9001, support.id)
    with pytest.raises(InvalidAction):
        await service.prepare_permission(9001, 101, Role.ADMIN, Permission.REFUND, True)
    revoke = await service.prepare_permission(
        9001, 101, Role.SUPPORT, Permission.MANAGE_TICKETS, False
    )
    await service.confirm(9001, revoke.id)
    admin = await service.prepare_permission(9001, 101, Role.ADMIN, Permission.REFUND, True)
    await service.confirm(9001, admin.id)
    current = await service.admin_access(101)
    assert current.role == Role.ADMIN
    assert current.permissions == {Permission.REFUND}
