from functools import partial

import pytest
from sqlalchemy import text

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.errors import AccessDenied, InvalidAction, InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.identity import CustomerIdentity, Role
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.manual import manual_service, waiting_manual


@pytest.mark.asyncio
async def test_manual_grant_confirmation_and_encrypted_evidence(
    migrated_database,
):
    engine = migrated_database
    order, work_id = await waiting_manual(engine)
    service = manual_service(engine)
    with pytest.raises(AccessDenied):
        await service.detail(101, work_id)
    with pytest.raises(AccessDenied):
        await service.prepare_completion(101, work_id, "FAKE evidence")
    assert (await service.queue(9001)).items[0].work_id == work_id
    detail = await service.detail(9001, work_id)
    assert detail.order_id == order.id and detail.destination_telegram_id == 101
    assert detail.staff_instructions == "FAKE private staff instructions"
    assert "FAKE" not in repr(detail)
    with pytest.raises(InvalidInput):
        await service.prepare_completion(9001, work_id, "")
    action = await service.prepare_completion(9001, work_id, "FAKE private evidence 123")
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "AWAITING_MANUAL"
        assert b"FAKE" not in await connection.scalar(text("SELECT ciphertext FROM manual_actions"))
    assert await service.confirm(9001, action.id) is True
    assert await service.confirm(9001, action.id) is False
    assert (await service.queue(9001)).items == ()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "SENT"
        assert await connection.scalar(text("SELECT status FROM orders")) == "DELIVERED"
        changes = str((await connection.execute(text("SELECT changes FROM audit_logs"))).all())
        assert "private evidence" not in changes and "staff instructions" not in changes
        assert await connection.scalar(text("SELECT count(*) FROM delivery_attempts")) == 1


@pytest.mark.asyncio
async def test_manual_completion_rechecks_revoked_actor_and_never_uses_orders_read_as_write_grant(
    migrated_database,
):
    engine = migrated_database
    _, work_id = await waiting_manual(engine)
    factory = partial(unit_of_work, engine)
    await IdentityService(factory).get_or_create_customer(CustomerIdentity(200, None, "Fake staff"))
    access = AccessService(factory)
    grant = await access.prepare_permission(9001, 200, Role.ADMIN, Permission.VIEW_ORDERS, True)
    await access.confirm(9001, grant.id)
    service = manual_service(engine)
    with pytest.raises(AccessDenied):
        await service.detail(200, work_id)
    grant = await access.prepare_permission(
        9001, 200, Role.ADMIN, Permission.MANAGE_FULFILLMENT, True
    )
    await access.confirm(9001, grant.id)
    action = await service.prepare_completion(200, work_id, "FAKE evidence")
    revoke = await access.prepare_permission(
        9001, 200, Role.ADMIN, Permission.MANAGE_FULFILLMENT, False
    )
    await access.confirm(9001, revoke.id)
    with pytest.raises(AccessDenied):
        await service.confirm(200, action.id)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, action.id)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "FULFILLING"
