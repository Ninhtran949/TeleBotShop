import asyncio
from dataclasses import replace
from datetime import timedelta
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.domain.models.orders import InvoiceStatus, OrderStatus
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.orders import historical_order, order_service, setup_orders
from tests.pre_checkout import checkout_request, payable_invoice


def service(engine):
    return PreCheckoutService(partial(unit_of_work, engine))


@pytest.mark.asyncio
async def test_approval_extends_exact_reservation_atomically_without_marking_paid(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await historical_order(engine, product, age=timedelta(minutes=5))
    await payable_invoice(engine, order)
    request = checkout_request(order)
    assert await service(engine).check(request) is True
    extended = await order_service(engine).get(101, order.id)
    assert extended.status == OrderStatus.AWAITING_PAYMENT
    assert extended.price_stars == order.price_stars
    assert extended.expires_at > order.expires_at
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT query_id, approved_at FROM pre_checkout_approvals")
            )
        ).one()
        assert row.query_id == request.query_id
        assert extended.expires_at == row.approved_at + timedelta(minutes=15)
        assert (
            await connection.scalar(text("SELECT reservation_expires_at FROM inventory_units"))
            == extended.expires_at
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action = 'pre_checkout.approved'")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_same_query_is_idempotent_and_different_query_is_rejected(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payable_invoice(engine, order)
    request = checkout_request(order)
    pending = None
    try:
        async with engine.begin() as blocker:
            await blocker.execute(text("SELECT id FROM orders FOR UPDATE"))
            pending = asyncio.gather(service(engine).check(request), service(engine).check(request))
            async with asyncio.timeout(1.5):
                while True:
                    pids = (
                        await blocker.scalars(
                            text(
                                "SELECT DISTINCT pid FROM pg_locks "
                                "WHERE relation = 'orders'::regclass "
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
    assert results == [True, True]
    first = await order_service(engine).get(101, order.id)
    assert await service(engine).check(checkout_request(order, query_id="another_query")) is False
    assert await service(engine).check(request) is True
    assert await order_service(engine).get(101, order.id) == first
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM pre_checkout_approvals")) == 1
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action = 'pre_checkout.approved'")
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [InvoiceStatus.SENDING, InvoiceStatus.UNCERTAIN])
async def test_invoice_with_pending_or_uncertain_send_can_be_validated(migrated_database, state):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payable_invoice(engine, order, state)
    assert await service(engine).check(checkout_request(order)) is True
    assert (await order_service(engine).get(101, order.id)).invoice_status == state


@pytest.mark.asyncio
async def test_invoice_rejection_after_approval_preserves_reservation_for_reconciliation(
    migrated_database,
):
    from telebotshop.application.services.invoices import InvoiceService
    from telebotshop.domain.models.orders import InvoiceDelivery

    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())

    class ContradictorySender:
        calls = 0

        async def send(self, request):
            assert engine.pool.checkedout() == 0
            self.calls += 1
            assert await service(engine).check(checkout_request(request.order)) is True
            return InvoiceDelivery(InvoiceStatus.REJECTED)

    sender = ContradictorySender()
    invoices = InvoiceService(partial(unit_of_work, engine), sender)
    result = await invoices.send(101, order.id)
    assert result.status == OrderStatus.AWAITING_PAYMENT
    assert result.invoice_status == InvoiceStatus.UNCERTAIN
    assert await invoices.send(101, order.id) == result
    assert sender.calls == 1
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "RESERVED"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["buyer", "unregistered", "amount", "payload", "unsent"])
async def test_mismatched_checkout_never_creates_approval_or_extends_stock(
    migrated_database, change
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    if change != "unsent":
        await payable_invoice(engine, order)
    changes = {
        "buyer": {"buyer_telegram_id": 102},
        "unregistered": {"buyer_telegram_id": 9999},
        "amount": {"amount": 101},
        "payload": {"payload": "z" * 32},
        "unsent": {},
    }
    assert await service(engine).check(replace(checkout_request(order), **changes[change])) is False
    assert (await order_service(engine).get(101, order.id)).expires_at == order.expires_at
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM pre_checkout_approvals")) == 0


@pytest.mark.asyncio
async def test_query_is_globally_unique_and_two_queries_cannot_approve_same_order(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine, 2)
    orders = order_service(engine)
    first = await orders.create(101, product.id, uuid4())
    second = await orders.create(101, product.id, uuid4())
    await payable_invoice(engine, first)
    await payable_invoice(engine, second)
    results = await asyncio.gather(
        service(engine).check(checkout_request(first)),
        service(engine).check(checkout_request(second)),
    )
    assert sorted(results) == [False, True]
    loser = second if results[0] else first
    result = await asyncio.gather(
        service(engine).check(checkout_request(loser, query_id="new_query_1")),
        service(engine).check(checkout_request(loser, query_id="new_query_2")),
    )
    assert sorted(result) == [False, True]
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM pre_checkout_approvals")) == 2


@pytest.mark.asyncio
async def test_current_product_state_and_snapshot_price_are_authoritative(migrated_database):
    from telebotshop.application.services.catalog import CatalogService
    from telebotshop.domain.models.catalog import SaleState

    engine = migrated_database
    product = await setup_orders(engine, 2)
    orders = order_service(engine)
    first = await orders.create(101, product.id, uuid4())
    second = await orders.create(101, product.id, uuid4())
    await payable_invoice(engine, first)
    await payable_invoice(engine, second)
    catalog = CatalogService(partial(unit_of_work, engine))
    edit = await catalog.prepare_price(9001, "DEMO-1", 450)
    await catalog.confirm(9001, edit.id)
    assert await service(engine).check(checkout_request(first, amount=450)) is False
    assert await service(engine).check(checkout_request(first)) is True
    hide = await catalog.prepare_sale(9001, "DEMO-1", SaleState.HIDDEN)
    await catalog.confirm(9001, hide.id)
    assert await service(engine).check(checkout_request(second, query_id="hidden_query")) is False
    assert await service(engine).check(checkout_request(first)) is False


@pytest.mark.asyncio
async def test_approval_cancel_race_and_reallocated_unit_never_approve_old_order(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    orders = order_service(engine)
    first = await orders.create(101, product.id, uuid4())
    await payable_invoice(engine, first)
    await asyncio.gather(
        service(engine).check(checkout_request(first)), orders.cancel(101, first.id)
    )
    second = await orders.create(102, product.id, uuid4())
    await payable_invoice(engine, second)
    assert await service(engine).check(checkout_request(first)) is False
    assert await orders.expire() == 0
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT reservation_item_id FROM inventory_units"))
            == second.item_id
        )


@pytest.mark.asyncio
async def test_expired_and_wrong_reservation_fail_closed(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    expired = await historical_order(engine, product)
    await payable_invoice(engine, expired)
    assert await service(engine).check(checkout_request(expired)) is False
    await order_service(engine).expire()
    current = await order_service(engine).create(102, product.id, uuid4())
    corrupt = replace(current, id=uuid4(), item_id=uuid4(), payload="c" * 32)
    async with unit_of_work(engine) as work:
        # Valid SQL shape with a missing reservation exercises fail-closed reads.
        await work.orders.save(corrupt, uuid4())
        await work.commit()
    await payable_invoice(engine, corrupt)
    assert await service(engine).check(checkout_request(corrupt, buyer=102)) is False
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT reservation_item_id FROM inventory_units"))
            == current.item_id
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_approval_audit_failure_rolls_back_binding_both_expiries_and_audit(
    migrated_database,
    deferred,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await historical_order(engine, product, age=timedelta(minutes=5))
    await payable_invoice(engine, order)
    async with engine.begin() as connection:
        before = await connection.scalar(text("SELECT count(*) FROM audit_logs"))
        await connection.execute(
            text("""CREATE FUNCTION fail_pre_checkout() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected approval failure'; END; $$""")
        )
        trigger = (
            "CREATE CONSTRAINT TRIGGER fail_approval AFTER INSERT ON audit_logs "
            "DEFERRABLE INITIALLY DEFERRED"
            if deferred
            else "CREATE TRIGGER fail_approval BEFORE INSERT ON audit_logs"
        )
        await connection.execute(
            text(
                trigger + " FOR EACH ROW "
                "WHEN (NEW.action = 'pre_checkout.approved') EXECUTE FUNCTION fail_pre_checkout()"
            )
        )
    with pytest.raises(DBAPIError):
        await service(engine).check(checkout_request(order))
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM pre_checkout_approvals")) == 0
        assert await connection.scalar(text("SELECT expires_at FROM orders")) == order.expires_at
        assert (
            await connection.scalar(text("SELECT reservation_expires_at FROM inventory_units"))
            == order.expires_at
        )
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE pre_checkout_approvals SET query_id = 'forged'",
        "DELETE FROM pre_checkout_approvals",
        "TRUNCATE pre_checkout_approvals",
        "UPDATE orders SET expires_at = expires_at + interval '30 minutes'",
        "UPDATE inventory_units SET reservation_expires_at = "
        "reservation_expires_at + interval '1 minute'",
    ],
)
async def test_database_preserves_approval_binding_and_controlled_extension(migrated_database, sql):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payable_invoice(engine, order)
    assert await service(engine).check(checkout_request(order)) is True
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(text(sql))


@pytest.mark.asyncio
async def test_deadline_is_rechecked_after_reservation_lock_wait(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await historical_order(
        engine, product, age=timedelta(minutes=15) - timedelta(seconds=0.8)
    )
    await payable_invoice(engine, order)
    task = None
    try:
        async with engine.begin() as blocker:
            await blocker.execute(text("SELECT id FROM inventory_units FOR UPDATE"))
            task = asyncio.create_task(service(engine).check(checkout_request(order)))
            async with asyncio.timeout(1):
                while not await blocker.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE relation = 'inventory_units'::regclass "
                        "AND mode = 'RowShareLock' AND pid <> pg_backend_pid()"
                    )
                ):
                    await asyncio.sleep(0.01)
            await asyncio.sleep(0.9)
        assert await task is False
    finally:
        if task is not None and not task.done():
            await task
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM pre_checkout_approvals")) == 0


@pytest.mark.asyncio
async def test_expiry_skips_inflight_approval_and_observes_extended_ttl(
    migrated_database, monkeypatch
):
    from telebotshop.infrastructure.database.repositories.orders import SqlOrderRepository

    engine = migrated_database
    product = await setup_orders(engine)
    order = await historical_order(
        engine, product, age=timedelta(minutes=15) - timedelta(seconds=0.8)
    )
    await payable_invoice(engine, order)
    entered, release = asyncio.Event(), asyncio.Event()
    original = SqlOrderRepository.audit

    async def audit(repository, order, action):
        await original(repository, order, action)
        if action == "pre_checkout.approved":
            entered.set()
            await release.wait()

    monkeypatch.setattr(SqlOrderRepository, "audit", audit)
    task = asyncio.create_task(service(engine).check(checkout_request(order)))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0.9)
        assert await order_service(engine).expire() == 0
    finally:
        release.set()
    assert await task is True
    assert await order_service(engine).expire() == 0
    current = await order_service(engine).get(101, order.id)
    assert current.status == OrderStatus.AWAITING_PAYMENT
    assert current.expires_at > order.expires_at
