from dataclasses import replace
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError

from telebotshop.application.services.catalog import CatalogService
from telebotshop.domain.errors import InvalidAction, InvalidInput
from telebotshop.domain.models.catalog import (
    DigitalFileMetadata,
    LicenseMetadata,
    ProductType,
    SaleState,
)
from telebotshop.domain.models.inventory import ImportFormat
from telebotshop.infrastructure.database.models.inventory import inventory_units
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.catalog import fake_product
from tests.inventory import inventory_service, setup_inventory


@pytest.mark.asyncio
async def test_full_batch_pagination_counts_and_per_sku_duplicates_use_committed_units(
    migrated_database,
):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    content = "\n".join(f"FAKE-{index}" for index in range(1000)).encode()
    action = await service.prepare_import(9001, "DEMO-1", content, ImportFormat.TXT)
    assert (await service.low_stock(9001)).items[0].available == 0
    await service.confirm(9001, action.id)
    first, second = await service.stock(9001, "DEMO-1"), await service.stock(9001, "DEMO-1", 1)
    assert first.summary.available == 1000 and not first.summary.low_stock
    assert len(first.units.items) == 5 and first.units.has_more
    assert {unit.id for unit in first.units.items}.isdisjoint(
        unit.id for unit in second.units.items
    )
    assert (await service.low_stock(9001)).items == ()
    catalog = CatalogService(partial(unit_of_work, migrated_database))
    draft = await catalog.prepare_product(9001, fake_product("DEMO-2"))
    await catalog.confirm(9001, draft.id)
    other = await service.prepare_import(9001, "DEMO-2", b"FAKE-0", ImportFormat.TXT)
    await service.confirm(9001, other.id)
    assert (await service.stock(9001, "DEMO-2")).summary.available == 1
    assert not (await service.stock(9001, "DEMO-2")).summary.low_stock
    for page in (-1, True, 10000):
        with pytest.raises(InvalidInput):
            await service.stock(9001, "DEMO-1", page)


@pytest.mark.asyncio
async def test_license_import_is_distinct_and_nonfinite_products_do_not_fake_stock(
    migrated_database,
):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    catalog = CatalogService(partial(unit_of_work, migrated_database))
    for sku, kind, metadata in (
        ("LICENSE", ProductType.LICENSE, LicenseMetadata("Hướng dẫn giả công khai")),
        ("FILE", ProductType.DIGITAL_FILE, DigitalFileMetadata("fake.pdf", "application/pdf")),
    ):
        action = await catalog.prepare_product(
            9001, replace(fake_product(sku), product_type=kind, metadata=metadata)
        )
        await catalog.confirm(9001, action.id)
        publish = await catalog.prepare_sale(9001, sku, SaleState.ACTIVE)
        await catalog.confirm(9001, publish.id)
    licensed = await service.prepare_import(
        9001, "LICENSE", b"license_key\nFAKE-LICENSE", ImportFormat.CSV
    )
    await service.confirm(9001, licensed.id)
    assert (await service.stock(9001, "LICENSE")).summary.available == 1
    with pytest.raises(InvalidAction):
        await service.prepare_import(9001, "FILE", b"FAKE-file-id", ImportFormat.TXT)
    with pytest.raises(InvalidAction):
        await service.stock(9001, "FILE")
    assert {summary.sku for summary in (await service.low_stock(9001)).items} == {
        "DEMO-1",
        "LICENSE",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "DELETE FROM inventory_units WHERE id = :id",
        "UPDATE inventory_units SET status = 'AVAILABLE' WHERE id = :id",
        "UPDATE inventory_units SET ciphertext = decode(repeat('00', 30), 'hex') WHERE id = :id",
        "UPDATE inventory_units SET fingerprint = decode(repeat('00', 32), 'hex') WHERE id = :id",
        "UPDATE inventory_units SET id = :replacement WHERE id = :id",
    ],
)
async def test_retired_unit_identity_ciphertext_and_dedup_cannot_be_released_or_deleted(
    migrated_database, change
):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    action = await service.prepare_import(9001, "DEMO-1", b"FAKE-PRIVATE", ImportFormat.TXT)
    await service.confirm(9001, action.id)
    unit = (await service.stock(9001, "DEMO-1")).units.items[0]
    retire = await service.prepare_retire(9001, "DEMO-1", unit.id)
    await service.confirm(9001, retire.id)
    with pytest.raises(IntegrityError):
        async with migrated_database.begin() as connection:
            await connection.execute(text(change), {"id": unit.id, "replacement": uuid4()})
    assert (await service.stock(9001, "DEMO-1")).summary.retired == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [False, True])
async def test_db_rejects_duplicate_material_and_wrong_product_type_without_application(
    migrated_database, mismatch
):
    await setup_inventory(migrated_database)
    service = inventory_service(migrated_database)
    action = await service.prepare_import(9001, "DEMO-1", b"FAKE-PRIVATE", ImportFormat.TXT)
    await service.confirm(9001, action.id)
    async with migrated_database.connect() as connection:
        copied = dict((await connection.execute(select(inventory_units))).mappings().one())
    copied.update(id=uuid4(), nonce=b"z" * 12)
    if mismatch:
        copied.update(product_type="LICENSE", fingerprint=b"z" * 32)
    with pytest.raises(IntegrityError):
        async with migrated_database.begin() as connection:
            await connection.execute(insert(inventory_units).values(copied))
    assert (await service.stock(9001, "DEMO-1")).summary.available == 1
