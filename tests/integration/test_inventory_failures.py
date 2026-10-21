import asyncio
from functools import partial
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.inventory import InventoryService
from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.identity import CustomerIdentity, Role
from telebotshop.domain.models.inventory import (
    ImportFormat,
    InventoryCryptoError,
    InventoryImportError,
)
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.inventory import fake_cipher, inventory_service, setup_inventory


@pytest.mark.asyncio
async def test_cancel_of_applied_import_cannot_claim_to_undo_stock(migrated_database):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    action = await service.prepare_import(9001, "DEMO-1", b"FAKE-PRIVATE", ImportFormat.TXT)
    await service.confirm(9001, action.id)
    with pytest.raises(InvalidAction):
        await service.cancel(9001, action.id)
    assert (await service.stock(9001, "DEMO-1")).summary.available == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("operation", ["import", "retire"])
async def test_audit_or_commit_failure_rolls_back_stock_action_and_audit(
    migrated_database, deferred, operation
):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    imported = await service.prepare_import(9001, "DEMO-1", b"FAKE-private", ImportFormat.TXT)
    action = imported
    if operation == "retire":
        await service.confirm(9001, imported.id)
        unit = (await service.stock(9001, "DEMO-1")).units.items[0]
        action = await service.prepare_retire(9001, "DEMO-1", unit.id)
    async with migrated_database.begin() as connection:
        await connection.execute(
            text("""CREATE FUNCTION fail_inventory_audit() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Synthetic inventory audit failure'
        USING ERRCODE = '23514'; END; $$""")
        )
        trigger = (
            "CREATE CONSTRAINT TRIGGER fail_inventory AFTER INSERT ON audit_logs "
            "DEFERRABLE INITIALLY DEFERRED"
            if deferred
            else "CREATE TRIGGER fail_inventory BEFORE INSERT ON audit_logs"
        )
        await connection.execute(
            text(
                trigger
                + " FOR EACH ROW WHEN (NEW.action = 'inventory."
                + operation
                + "') EXECUTE FUNCTION fail_inventory_audit()"
            )
        )
    with pytest.raises(DBAPIError):
        await service.confirm(9001, action.id)
    stock = await service.stock(9001, "DEMO-1")
    assert stock.summary.available == (1 if operation == "retire" else 0)
    assert stock.summary.retired == 0
    async with migrated_database.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT status FROM inventory_actions WHERE id = :id"), {"id": action.id}
            )
            == "pending"
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE correlation_id = :id"),
                {"id": action.id},
            )
            == 0
        )


@pytest.mark.asyncio
async def test_duplicate_invalid_and_encryption_failure_leave_no_partial_pending_batch(
    migrated_database,
):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    for content in (b"FAKE-A\nFAKE-A", b"FAKE-A\n\nFAKE-B", b"x" * (1024 * 1024 + 1)):
        with pytest.raises(InventoryImportError):
            await service.prepare_import(9001, "DEMO-1", content, ImportFormat.TXT)
    real = fake_cipher()
    cipher = Mock(wraps=real)
    count = 0

    def seal(context, payload):
        nonlocal count
        count += 1
        if count == 2:
            raise InventoryCryptoError("Synthetic encryption failure")
        return real.seal(context, payload)

    cipher.seal.side_effect = seal
    failing = InventoryService(partial(unit_of_work, migrated_database), cipher, 5)
    with pytest.raises(InventoryCryptoError):
        await failing.prepare_import(9001, "DEMO-1", b"FAKE-A\nFAKE-B", ImportFormat.TXT)
    async with migrated_database.connect() as connection:
        for table in ("inventory_actions", "inventory_units", "inventory_crypto_state"):
            assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.asyncio
async def test_revoked_and_other_authorized_actor_cannot_apply_or_cancel_pending_import(
    migrated_database,
):
    await setup_inventory(migrated_database)
    factory = partial(unit_of_work, migrated_database)
    identity, access = IdentityService(factory), AccessService(factory)
    await identity.get_or_create_customer(CustomerIdentity(101, "fake_staff", "Fake staff"))
    grant = await access.prepare_permission(
        9001, 101, Role.ADMIN, Permission.MANAGE_INVENTORY, True
    )
    await access.confirm(9001, grant.id)
    service = inventory_service(migrated_database)
    action = await service.prepare_import(101, "DEMO-1", b"FAKE-private", ImportFormat.TXT)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, action.id)
    with pytest.raises(InvalidAction):
        await service.cancel(9001, action.id)
    revoke = await access.prepare_permission(
        9001, 101, Role.ADMIN, Permission.MANAGE_INVENTORY, False
    )
    await access.confirm(9001, revoke.id)
    for operation in (
        lambda: service.confirm(101, action.id),
        lambda: service.cancel(101, action.id),
        lambda: service.stock(101, "DEMO-1"),
        lambda: service.low_stock(101),
    ):
        with pytest.raises(AccessDenied):
            await operation()
    assert (await service.stock(9001, "DEMO-1")).summary.available == 0


@pytest.mark.asyncio
async def test_expired_and_malformed_pending_import_never_become_available(migrated_database):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    expired = await service.prepare_import(9001, "DEMO-1", b"FAKE-private", ImportFormat.TXT)
    async with migrated_database.begin() as connection:
        await connection.execute(
            text("UPDATE inventory_actions SET expires_at = now() - interval '1 second'")
        )
    with pytest.raises(InvalidAction):
        await service.confirm(9001, expired.id)
    await service.cancel(9001, expired.id)
    malformed = await service.prepare_import(9001, "DEMO-1", b"FAKE-other", ImportFormat.TXT)
    async with migrated_database.begin() as connection:
        await connection.execute(
            text("UPDATE inventory_actions SET envelopes = '[{}]'::jsonb WHERE id = :id"),
            {"id": malformed.id},
        )
    with pytest.raises(InventoryCryptoError):
        await service.confirm(9001, malformed.id)
    assert (await service.stock(9001, "DEMO-1")).summary.available == 0


@pytest.mark.asyncio
async def test_replayed_import_and_competing_retire_actions_have_one_effect(migrated_database):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    action = await service.prepare_import(9001, "DEMO-1", b"FAKE-private", ImportFormat.TXT)
    assert sorted(
        await asyncio.gather(service.confirm(9001, action.id), service.confirm(9001, action.id))
    ) == [False, True]
    unit = (await service.stock(9001, "DEMO-1")).units.items[0]
    first = await service.prepare_retire(9001, "DEMO-1", unit.id)
    second = await service.prepare_retire(9001, "DEMO-1", unit.id)
    results = await asyncio.gather(
        service.confirm(9001, first.id), service.confirm(9001, second.id), return_exceptions=True
    )
    assert sum(result is True for result in results) == 1
    assert sum(isinstance(result, InvalidAction) for result in results) == 1
    async with migrated_database.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action = 'inventory.retire'")
            )
            == 1
        )
