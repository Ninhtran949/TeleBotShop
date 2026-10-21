import asyncio
from dataclasses import replace
from datetime import timedelta
from functools import partial
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from telebotshop.application.services.catalog import CatalogService
from telebotshop.domain.errors import AccessDenied, CatalogNotFound, InvalidAction, OutOfStock
from telebotshop.domain.models.catalog import (
    DigitalFileMetadata,
    LicenseMetadata,
    ManualMetadata,
    ProductType,
    ResourceFormat,
    ReusableMetadata,
    SaleState,
)
from telebotshop.domain.models.inventory import ImportFormat
from telebotshop.domain.models.orders import InvoiceStatus, OrderStatus
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.inventory import inventory_service
from tests.orders import historical_order, order_service, setup_orders


@pytest.mark.asyncio
async def test_create_reserves_one_unit_with_immutable_snapshot_and_durable_intent(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine)
    service = order_service(engine)
    action_id = uuid4()
    order = await service.create(101, product.id, action_id)
    assert order.status == OrderStatus.AWAITING_PAYMENT
    assert order.invoice_status == InvoiceStatus.READY
    assert order.price_stars == 100
    assert order.quantity == 1
    assert order.title == product.draft.name
    assert order.expires_at - order.created_at == timedelta(minutes=15)
    assert await service.create(101, product.id, action_id) == order
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM order_items")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM invoice_intents")) == 1
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "RESERVED"
        assert await connection.scalar(text("SELECT status FROM orders")) == "AWAITING_PAYMENT"
    stock = await inventory_service(engine).stock(9001, "DEMO-1")
    assert (stock.summary.available, stock.summary.reserved, stock.summary.retired) == (0, 1, 0)


@pytest.mark.asyncio
async def test_double_click_uses_buyer_action_key_and_last_key_has_one_winner(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    service = order_service(engine)
    action = uuid4()
    first, second = await asyncio.gather(
        service.create(101, product.id, action), service.create(101, product.id, action)
    )
    assert first.id == second.id
    with pytest.raises(OutOfStock):
        await service.create(102, product.id, action)


@pytest.mark.asyncio
async def test_two_buyers_race_for_last_key(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    service = order_service(engine)
    pending = None
    try:
        async with engine.begin() as blocker:
            await blocker.execute(
                text("SELECT id FROM products WHERE id = :id FOR UPDATE"), {"id": product.id}
            )
            pending = asyncio.gather(
                service.create(101, product.id, uuid4()),
                service.create(102, product.id, uuid4()),
                return_exceptions=True,
            )
            async with asyncio.timeout(1.5):
                while True:
                    pids = (
                        await blocker.scalars(
                            text(
                                "SELECT DISTINCT pid FROM pg_locks "
                                "WHERE relation = 'products'::regclass "
                                "AND mode = 'RowShareLock' AND pid <> pg_backend_pid()"
                            )
                        )
                    ).all()
                    if len(pids) == 2:
                        break
                    await asyncio.sleep(0.01)
        results = await pending
    finally:
        if pending is not None and not pending.done():
            await pending
    assert sum(isinstance(result, OutOfStock) for result in results) == 1
    assert sum(not isinstance(result, Exception) for result in results) == 1


@pytest.mark.asyncio
async def test_cancel_checks_owner_and_expired_action_never_reserves_again(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    service = order_service(engine)
    action = uuid4()
    order = await service.create(101, product.id, action)
    with pytest.raises(AccessDenied):
        await service.cancel(102, order.id)
    with pytest.raises(AccessDenied):
        await service.get(102, order.id)
    with pytest.raises(InvalidAction):
        await service.create(101, uuid4(), action)
    closed = await service.cancel(101, order.id)
    assert closed.status == OrderStatus.EXPIRED
    assert await service.cancel(101, order.id) == closed
    assert await service.create(101, product.id, action) == closed
    new_order = await service.create(102, product.id, uuid4())
    await service.expire()
    assert (await service.get(102, new_order.id)).status == OrderStatus.AWAITING_PAYMENT


@pytest.mark.asyncio
async def test_snapshot_survives_price_edit_and_new_action_gets_current_price(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine, 2)
    service = order_service(engine)
    action = uuid4()
    first = await service.create(101, product.id, action)
    catalog = CatalogService(partial(unit_of_work, engine))
    edit = await catalog.prepare_price(9001, product.draft.sku, 450)
    await catalog.confirm(9001, edit.id)
    assert (await service.create(101, product.id, action)).price_stars == 100
    assert (await service.get(101, first.id)).price_stars == 100
    assert (await service.create(101, product.id, uuid4())).price_stars == 450


@pytest.mark.asyncio
async def test_expiry_cancel_race_releases_once_and_old_expiry_cannot_release_new_order(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine)
    old = await historical_order(engine, product)
    service = order_service(engine)
    await asyncio.gather(service.expire(), service.cancel(101, old.id), service.expire())
    assert (await service.get(101, old.id)).status == OrderStatus.EXPIRED
    assert await service.expire() == 0
    new = await service.create(102, product.id, uuid4())
    await service.cancel(101, old.id)
    assert await service.expire() == 0
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT status, reservation_item_id FROM inventory_units")
            )
        ).one()
        assert (row.status, row.reservation_item_id) == ("RESERVED", new.item_id)
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM audit_logs WHERE action IN "
                    "('order.customer_cancelled', 'order.ttl_expired')"
                )
            )
            == 1
        )


@pytest.mark.asyncio
async def test_expiry_batch_is_bounded_and_skips_locked_orders(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine, 3)
    old = [await historical_order(engine, product) for _ in range(3)]
    service = order_service(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT id FROM orders WHERE id = :id FOR UPDATE"), {"id": old[0].id}
        )
        assert await service.expire(1) == 1
        assert await service.expire(1) == 1
        assert await service.expire(1) == 0
    assert await service.expire(1) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "cancel"])
async def test_commit_failure_rolls_back_order_reservation_intent_and_audit(
    migrated_database,
    operation,
):
    engine = migrated_database
    product = await setup_orders(engine)
    service = order_service(engine)
    order = await service.create(101, product.id, uuid4()) if operation == "cancel" else None
    async with engine.begin() as connection:
        before = await connection.scalar(text("SELECT count(*) FROM audit_logs"))
        await connection.execute(
            text("""CREATE FUNCTION fail_order_audit() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected commit failure'; END; $$""")
        )
        await connection.execute(
            text("""CREATE CONSTRAINT TRIGGER fail_order_commit
            AFTER INSERT ON audit_logs DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION fail_order_audit()""")
        )
    with pytest.raises(DBAPIError):
        if order is None:
            await service.create(101, product.id, uuid4())
        else:
            await service.cancel(101, order.id)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == before
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == int(
            order is not None
        )
        assert await connection.scalar(text("SELECT count(*) FROM invoice_intents")) == int(
            order is not None
        )
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == (
            "AVAILABLE" if order is None else "RESERVED"
        )
        if order is not None:
            assert await connection.scalar(text("SELECT status FROM orders")) == "AWAITING_PAYMENT"


@pytest.mark.asyncio
async def test_lock_contention_is_bounded_and_not_reported_as_out_of_stock(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT id FROM products WHERE id = :id FOR UPDATE"), {"id": product.id}
        )
        started = monotonic()
        with pytest.raises(DBAPIError) as failure:
            await order_service(engine).create(101, product.id, uuid4())
        assert failure.value.orig.sqlstate == "55P03"
        assert monotonic() - started < 6
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == 0
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "AVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE order_items SET price_stars = price_stars + 1",
        "UPDATE order_items SET quantity = 2",
        "UPDATE orders SET buyer_id = gen_random_uuid()",
        "UPDATE invoice_intents SET payload = repeat('x',32)",
        "UPDATE inventory_units SET status = 'AVAILABLE', reservation_item_id = NULL, "
        "reservation_expires_at = NULL",
        "UPDATE inventory_units SET status = 'RETIRED'",
        "UPDATE inventory_units SET reservation_item_id = gen_random_uuid()",
        "UPDATE inventory_units SET ciphertext = decode(repeat('aa', 20), 'hex')",
        "DELETE FROM orders",
        "DELETE FROM order_items",
        "DELETE FROM invoice_intents",
    ],
)
async def test_database_rejects_snapshot_or_reservation_tampering(migrated_database, sql):
    engine = migrated_database
    product = await setup_orders(engine)
    await order_service(engine).create(101, product.id, uuid4())
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(text(sql))


@pytest.mark.asyncio
async def test_retire_confirmation_cannot_consume_reserved_unit(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    inventory = inventory_service(engine)
    stock = await inventory.stock(9001, "DEMO-1")
    retire = await inventory.prepare_retire(9001, "DEMO-1", stock.units.items[0].id)
    await order_service(engine).create(101, product.id, uuid4())
    with pytest.raises(InvalidAction):
        await inventory.confirm(9001, retire.id)


@pytest.mark.asyncio
async def test_checkout_fails_closed_for_unregistered_customer_and_hidden_product(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine)
    with pytest.raises(AccessDenied):
        await order_service(engine).create(9999, product.id, uuid4())
    catalog = CatalogService(partial(unit_of_work, engine))
    hide = await catalog.prepare_sale(9001, "DEMO-1", SaleState.HIDDEN)
    await catalog.confirm(9001, hide.id)
    with pytest.raises(CatalogNotFound):
        await order_service(engine).create(101, product.id, uuid4())
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == 0
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "AVAILABLE"


@pytest.mark.asyncio
async def test_license_uses_same_reservation_without_decrypting_payload(migrated_database):
    engine = migrated_database
    original = await setup_orders(engine)
    catalog = CatalogService(partial(unit_of_work, engine))
    draft = replace(
        original.draft,
        sku="LICENSE-2",
        product_type=ProductType.LICENSE,
        metadata=LicenseMetadata("Hướng dẫn giả"),
    )
    action = await catalog.prepare_product(9001, draft)
    await catalog.confirm(9001, action.id)
    publish = await catalog.prepare_sale(9001, draft.sku, SaleState.ACTIVE)
    await catalog.confirm(9001, publish.id)
    inventory = inventory_service(engine)
    pending = await inventory.prepare_import(9001, draft.sku, b"FAKE-license", ImportFormat.TXT)
    await inventory.confirm(9001, pending.id)
    product = await catalog.admin_product(9001, draft.sku)
    order = await order_service(engine).create(101, product.id, uuid4())
    assert order.product_type == ProductType.LICENSE
    assert "FAKE-license" not in repr(order)
    assert (await inventory.stock(9001, draft.sku)).summary.reserved == 1


@pytest.mark.asyncio
async def test_downgrade_refuses_existing_order_history(migrated_database):
    from alembic import command
    from alembic.config import Config

    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:

            def downgrade(sync):
                config = Config("alembic.ini")
                config.attributes["connection"] = sync
                command.downgrade(config, "0005_inventory")

            await connection.run_sync(downgrade)
    assert (await order_service(engine).get(101, order.id)).status == OrderStatus.AWAITING_PAYMENT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "metadata"),
    [
        (ProductType.DIGITAL_FILE, DigitalFileMetadata("fake.pdf", "application/pdf")),
        (ProductType.REUSABLE_RESOURCE, ReusableMetadata(ResourceFormat.TEXT)),
        (ProductType.MANUAL_FULFILLMENT, ManualMetadata("Xử lý thủ công giả")),
    ],
)
async def test_nonfinite_checkout_is_closed_until_typed_resources_exist(
    migrated_database,
    kind,
    metadata,
):
    engine = migrated_database
    original = await setup_orders(engine)
    catalog = CatalogService(partial(unit_of_work, engine))
    draft = replace(original.draft, sku="OTHER-2", product_type=kind, metadata=metadata)
    action = await catalog.prepare_product(9001, draft)
    await catalog.confirm(9001, action.id)
    publish = await catalog.prepare_sale(9001, draft.sku, SaleState.ACTIVE)
    await catalog.confirm(9001, publish.id)
    product = await catalog.admin_product(9001, draft.sku)
    with pytest.raises(CatalogNotFound):
        await order_service(engine).create(101, product.id, uuid4())
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM orders")) == 0
