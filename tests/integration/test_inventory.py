import asyncio
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.inventory import (
    ImportFormat,
    InventoryContext,
    InventoryCryptoError,
    InventoryImportError,
    SealedPayload,
)
from telebotshop.infrastructure.crypto import AesGcmInventoryCipher
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.inventory import fake_cipher, inventory_service, setup_inventory


@pytest.mark.asyncio
async def test_import_is_encrypted_pending_then_atomic_stock_audit_and_dedup(migrated_database):
    item = await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    action = await service.prepare_import(
        9001, "DEMO-1", b"FAKE-PRIVATE-A\nFAKE-PRIVATE-B", ImportFormat.TXT
    )
    assert action.quantity == 2
    assert (await service.stock(9001, "DEMO-1")).summary.available == 0
    async with migrated_database.connect() as connection:
        pending = await connection.scalar(text("SELECT envelopes::text FROM inventory_actions"))
        assert "FAKE-PRIVATE" not in pending
    assert await service.confirm(9001, action.id) is True
    assert await service.confirm(9001, action.id) is False
    stock = await service.stock(9001, "DEMO-1")
    assert stock.summary.available == 2 and stock.summary.low_stock
    assert not hasattr(stock.units.items[0], "ciphertext")
    async with migrated_database.connect() as connection:
        row = (await connection.execute(text("SELECT * FROM inventory_units LIMIT 1"))).one()
        context = InventoryContext(item.id, "DEMO-1", row.id, item.draft.product_type)
        secret = fake_cipher().open(
            context, SealedPayload(row.key_version, row.nonce, row.ciphertext)
        )
        assert secret.key in ("FAKE-PRIVATE-A", "FAKE-PRIVATE-B")
        assert await connection.scalar(text("SELECT envelopes FROM inventory_actions")) is None
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action = 'inventory.import'")
            )
            == 1
        )
    duplicate = await service.prepare_import(
        9001, "DEMO-1", b"FAKE-new\nFAKE-PRIVATE-A", ImportFormat.TXT
    )
    with pytest.raises(InventoryImportError) as captured:
        await service.confirm(9001, duplicate.id)
    assert (captured.value.row, captured.value.code) == (2, "duplicate")
    assert (await service.stock(9001, "DEMO-1")).summary.available == 2


@pytest.mark.asyncio
async def test_parallel_reverse_order_imports_are_atomic_with_one_winner(migrated_database):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    first, second = await asyncio.gather(
        service.prepare_import(9001, "DEMO-1", b"FAKE-A\nFAKE-B", ImportFormat.TXT),
        service.prepare_import(9001, "DEMO-1", b"FAKE-B\nFAKE-A", ImportFormat.TXT),
    )
    connections = set()

    def record_connection(connection, _cursor, statement, _parameters, _context, _many):
        if "inventory_crypto_state" in statement:
            connections.add(id(connection.connection.driver_connection))

    event.listen(migrated_database.sync_engine, "before_cursor_execute", record_connection)
    try:
        results = await asyncio.gather(
            service.confirm(9001, first.id),
            service.confirm(9001, second.id),
            return_exceptions=True,
        )
    finally:
        event.remove(migrated_database.sync_engine, "before_cursor_execute", record_connection)
    assert len(connections) == 2
    assert sum(result is True for result in results) == 1
    assert sum(isinstance(result, InventoryImportError) for result in results) == 1
    assert (await service.stock(9001, "DEMO-1")).summary.available == 2


@pytest.mark.asyncio
async def test_permissions_cancel_cross_actor_and_retire_preserve_unique_material(
    migrated_database,
):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    with pytest.raises(AccessDenied):
        await service.prepare_import(101, "DEMO-1", b"FAKE-private", ImportFormat.TXT)
    action = await service.prepare_import(9001, "DEMO-1", b"FAKE-private", ImportFormat.TXT)
    with pytest.raises(AccessDenied):
        await service.confirm(101, action.id)
    await service.cancel(9001, action.id)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, action.id)
    action = await service.prepare_import(9001, "DEMO-1", b"FAKE-private", ImportFormat.TXT)
    await service.confirm(9001, action.id)
    stock = await service.stock(9001, "DEMO-1")
    retire = await service.prepare_retire(9001, "DEMO-1", stock.units.items[0].id)
    assert (await service.stock(9001, "DEMO-1")).summary.available == 1
    assert await service.confirm(9001, retire.id) is True
    assert await service.confirm(9001, retire.id) is False
    assert (await service.stock(9001, "DEMO-1")).summary.retired == 1
    assert (await service.low_stock(9001)).items[0].available == 0
    again = await service.prepare_import(9001, "DEMO-1", b"FAKE-private", ImportFormat.TXT)
    with pytest.raises(InventoryImportError):
        await service.confirm(9001, again.id)
    with pytest.raises(InvalidAction):
        await service.prepare_retire(9001, "DEMO-1", uuid4())


@pytest.mark.asyncio
async def test_key_configuration_guard_refuses_accidental_dedup_or_version_changes(
    migrated_database,
):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    await service.check_keys()
    for cipher in (
        AesGcmInventoryCipher({1: b"a" * 32}, 1, b"e" * 32),
        AesGcmInventoryCipher({1: b"b" * 32}, 1, b"d" * 32),
        AesGcmInventoryCipher({2: b"b" * 32}, 2, b"d" * 32),
    ):
        from telebotshop.application.services.inventory import InventoryService

        changed = InventoryService(partial(unit_of_work, migrated_database), cipher, 5)
        with pytest.raises(InventoryCryptoError):
            await changed.prepare_import(9001, "DEMO-1", b"FAKE-private", ImportFormat.TXT)
