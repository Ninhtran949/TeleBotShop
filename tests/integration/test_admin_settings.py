from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.errors import AccessDenied, InvalidAction, InvalidInput
from telebotshop.infrastructure.database.uow import unit_of_work


@pytest.mark.asyncio
async def test_safe_settings_are_owner_only_confirmed_versioned_and_audited(
    migrated_database: AsyncEngine,
) -> None:
    factory = partial(unit_of_work, migrated_database)
    await IdentityService(factory).bootstrap_owner(9001)
    service = AccessService(factory)
    with pytest.raises(AccessDenied):
        await service.settings(101)
    with pytest.raises(AccessDenied):
        await service.prepare_support(101, "fake_support")
    with pytest.raises(InvalidInput):
        await service.prepare_support(9001, "https://not-allowed.test/token=secret")
    assert (await service.settings(9001)).support_username is None
    action = await service.prepare_support(9001, "fake_support")
    stale = await service.prepare_support(9001, "fake_other")
    assert (await service.settings(9001)).support_username is None
    await service.confirm(9001, action.id)
    assert (await service.settings(9001)).support_username == "fake_support"
    assert not await service.confirm(9001, action.id)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, stale.id)
    clear = await service.prepare_support(9001, None)
    await service.confirm(9001, clear.id)
    assert (await service.settings(9001)).support_username is None
    async with migrated_database.connect() as connection:
        changes = (await connection.scalars(text("SELECT changes::text FROM audit_logs"))).all()
        assert len(changes) == 3
        assert all(
            "fake_support" not in change and "fake_other" not in change for change in changes
        )
