import asyncio
from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.errors import OwnerConfigurationError
from telebotshop.domain.models.identity import CustomerIdentity
from telebotshop.infrastructure.database.uow import unit_of_work


@pytest.mark.asyncio
async def test_concurrent_start_and_renamed_profile_keep_identity_and_registration(
    migrated_database: AsyncEngine,
) -> None:
    service = IdentityService(partial(unit_of_work, migrated_database))
    first, second = await asyncio.gather(
        service.get_or_create_customer(CustomerIdentity(101, "fake_first", "First")),
        service.get_or_create_customer(CustomerIdentity(101, "fake_second", "Second")),
    )
    assert first.id == second.id
    assert first.registered_at == second.registered_at
    renamed = await service.get_or_create_customer(CustomerIdentity(101, "fake_new", "New", "en"))
    assert renamed.id == first.id
    assert renamed.registered_at == first.registered_at
    assert renamed.username == "fake_new"
    assert renamed.language == "en"
    assert renamed.last_seen_at >= renamed.registered_at
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM users")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM user_roles")) == 0


@pytest.mark.asyncio
async def test_bootstrap_is_concurrent_idempotent_and_rejects_owner_change(
    migrated_database: AsyncEngine,
) -> None:
    service = IdentityService(partial(unit_of_work, migrated_database))
    await asyncio.gather(service.bootstrap_owner(9001), service.bootstrap_owner(9001))
    with pytest.raises(OwnerConfigurationError, match="Owner configuration mismatch"):
        await service.bootstrap_owner(9002)
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM users")) == 1
        assert (
            await connection.scalar(text("SELECT count(*) FROM user_roles WHERE role = 'OWNER'"))
            == 1
        )
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 1


@pytest.mark.asyncio
async def test_bootstrap_does_not_restore_a_removed_owner_role(
    migrated_database: AsyncEngine,
) -> None:
    service = IdentityService(partial(unit_of_work, migrated_database))
    await service.bootstrap_owner(9001)
    async with migrated_database.begin() as connection:
        await connection.execute(text("DELETE FROM user_roles WHERE role = 'OWNER'"))
    with pytest.raises(OwnerConfigurationError):
        await service.bootstrap_owner(9001)
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM user_roles")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 1


@pytest.mark.asyncio
async def test_competing_initial_owner_ids_cannot_create_two_owners(
    migrated_database: AsyncEngine,
) -> None:
    service = IdentityService(partial(unit_of_work, migrated_database))
    results = await asyncio.gather(
        service.bootstrap_owner(9001), service.bootstrap_owner(9002), return_exceptions=True
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, OwnerConfigurationError) for result in results) == 1
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM users")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM user_roles")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 1
