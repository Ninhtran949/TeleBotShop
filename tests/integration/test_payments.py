import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.payments import PaymentService
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.orders import OrderStatus
from telebotshop.domain.models.payments import PaymentOutcome
from telebotshop.infrastructure.database.repositories.payments import SqlPaymentRepository
from telebotshop.infrastructure.database.uow import SqlAlchemyUnitOfWork, unit_of_work
from tests.inventory import inventory_service
from tests.orders import historical_order, order_service, setup_orders
from tests.payments import payment_service, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_charge", ["", "FAKE-provider-reference"])
async def test_matched_receipt_paid_allocation_and_one_committed_work_are_atomic(
    migrated_database,
    provider_charge,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    result = await payment_service(engine).record(
        replace(receipt(order), provider_charge_id=provider_charge)
    )
    assert result.outcome == PaymentOutcome.MATCHED
    assert (await order_service(engine).get(101, order.id)).status == OrderStatus.PAID
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM payments")) == "MATCHED"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
        assert (
            await connection.scalar(text("SELECT allocated_item_id FROM inventory_units"))
            == order.item_id
        )
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1
        work = (
            await connection.execute(
                text(
                    "SELECT order_id, order_item_id, inventory_unit_id, "
                    "destination_telegram_id, state FROM fulfillments"
                )
            )
        ).one()
        assert tuple(work) == (order.id, order.item_id, order.unit_id, 101, "READY")


@pytest.mark.asyncio
async def test_duplicate_charge_and_extra_charge_do_not_allocate_twice(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    service = payment_service(engine)
    first, second = await asyncio.gather(
        service.record(receipt(order)), service.record(receipt(order))
    )
    assert {first.outcome, second.outcome} == {PaymentOutcome.MATCHED, PaymentOutcome.DUPLICATE}
    assert first.payment_id == second.payment_id
    extra = await service.record(receipt(order, charge="FAKE-extra-charge"))
    assert extra.outcome == PaymentOutcome.REQUIRES_REVIEW
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM payments")) == 2
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("new_paid", [False, True])
async def test_late_valid_receipt_after_release_is_paid_attention_and_never_steals_unit(
    migrated_database,
    new_paid,
):
    engine = migrated_database
    product = await setup_orders(engine)
    orders = order_service(engine)
    old = await orders.create(101, product.id, uuid4())
    await orders.cancel(101, old.id)
    current = await orders.create(102, product.id, uuid4())
    if new_paid:
        await payment_service(engine).record(receipt(current, charge="FAKE-new-owner", payer=102))
    result = await payment_service(engine).record(receipt(old))
    assert result.outcome == PaymentOutcome.MATCHED
    paid = await orders.get(101, old.id)
    assert paid.status == OrderStatus.PAID and paid.attention_reason == "RESERVATION_LOST"
    async with engine.connect() as connection:
        binding = "allocated_item_id" if new_paid else "reservation_item_id"
        assert (
            await connection.scalar(text(f"SELECT {binding} FROM inventory_units"))
            == current.item_id
        )
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == int(new_paid)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"payload": "unknown"}, "UNKNOWN_ORDER"),
        ({"payer_id": None}, "MISSING_PAYER"),
        ({"payer_id": 102}, "WRONG_BUYER"),
        ({"payer_id": 999}, "WRONG_BUYER"),
        ({"currency": "USD"}, "WRONG_CURRENCY"),
        ({"amount": 0}, "WRONG_AMOUNT"),
        ({"amount": 101}, "WRONG_AMOUNT"),
        ({"is_recurring": True}, "UNSUPPORTED"),
        ({"is_first_recurring": True}, "UNSUPPORTED"),
        ({"subscription_expiration_date": 1}, "UNSUPPORTED"),
        ({"has_order_info": True}, "UNSUPPORTED"),
        ({"shipping_option_id": "fake"}, "UNSUPPORTED"),
    ],
)
async def test_invalid_or_unsupported_receipts_are_durable_review_without_allocating(
    migrated_database,
    changes,
    reason,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    evidence = replace(receipt(order), **changes)
    result = await payment_service(engine).record(evidence)
    assert result.outcome == PaymentOutcome.REQUIRES_REVIEW
    assert (await payment_service(engine).record(evidence)).outcome == PaymentOutcome.DUPLICATE
    assert (await order_service(engine).get(101, order.id)).status == OrderStatus.AWAITING_PAYMENT
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT review_reason FROM payments")) == reason
        assert await connection.scalar(text("SELECT count(*) FROM payments")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 0
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "RESERVED"
        assert (
            await connection.scalar(text("SELECT count(*) FROM users WHERE telegram_id = 999")) == 0
        )


@pytest.mark.asyncio
async def test_conflicting_charge_replay_cannot_overwrite_receipt_or_credit_another_buyer(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine, 2)
    orders = order_service(engine)
    first = await orders.create(101, product.id, uuid4())
    second = await orders.create(102, product.id, uuid4())
    payments = payment_service(engine)
    canonical = await payments.record(receipt(first))
    conflict = receipt(second, payer=102)
    results = await asyncio.gather(payments.record(conflict), payments.record(conflict))
    assert all(
        r.outcome == PaymentOutcome.CONFLICT and r.payment_id == canonical.payment_id
        for r in results
    )
    assert (await orders.get(102, second.id)).status == OrderStatus.AWAITING_PAYMENT
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT order_id FROM payments")) == first.id
        assert await connection.scalar(text("SELECT count(*) FROM payments")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM payment_conflicts")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1


@pytest.mark.asyncio
async def test_same_charge_race_serializes_two_connections_before_order_binding(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine, 2)
    first_order = await order_service(engine).create(101, product.id, uuid4())
    second_order = await order_service(engine).create(102, product.id, uuid4())
    locked, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def held_uow():
        async with unit_of_work(engine) as work:
            original = work.payments.lock_charge

            async def hold(charge):
                await original(charge)
                locked.set()
                await release.wait()

            work.payments.lock_charge = hold
            yield work

    first = asyncio.create_task(PaymentService(held_uow).record(receipt(first_order)))
    second = None
    try:
        await asyncio.wait_for(locked.wait(), 1.5)
        second = asyncio.create_task(
            payment_service(engine).record(receipt(second_order, payer=102))
        )
        async with engine.connect() as connection:
            async with asyncio.timeout(1.5):
                while True:
                    rows = (
                        await connection.execute(
                            text(
                                "SELECT pid, granted FROM pg_locks WHERE locktype = 'advisory' "
                                "AND pid IN (SELECT pid FROM pg_stat_activity "
                                "WHERE datname = current_database())"
                            )
                        )
                    ).all()
                    if len({row.pid for row in rows}) == 2 and {row.granted for row in rows} == {
                        False,
                        True,
                    }:
                        break
                    await asyncio.sleep(0.01)
        release.set()
        assert (await first).outcome == PaymentOutcome.MATCHED
        assert (await second).outcome == PaymentOutcome.CONFLICT
        assert (
            await order_service(engine).get(102, second_order.id)
        ).status == OrderStatus.AWAITING_PAYMENT
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM payments")) == 1
            assert await connection.scalar(text("SELECT count(*) FROM payment_conflicts")) == 1
            assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1
    finally:
        release.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)


@pytest.mark.asyncio
async def test_snapshot_price_is_used_and_elapsed_ttl_does_not_release_owned_reservation(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await historical_order(engine, product)
    catalog = CatalogService(partial(unit_of_work, engine))
    action = await catalog.prepare_price(9001, "DEMO-1", 300)
    await catalog.confirm(9001, action.id)
    result = await payment_service(engine).record(receipt(order))
    assert result.outcome == PaymentOutcome.MATCHED and result.attention_reason is None
    assert await order_service(engine).expire() == 0
    with pytest.raises(InvalidAction):
        await order_service(engine).cancel(101, order.id)
    stock = (await inventory_service(engine).stock(9001, "DEMO-1")).summary
    assert (stock.available, stock.reserved, stock.allocated) == (0, 0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["mark_paid", "allocate", "save_work", "audit", "commit"])
async def test_failure_at_every_atomic_boundary_rolls_back_and_retry_allocates_once(
    migrated_database,
    stage,
    monkeypatch,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())

    async def fail(*args, **kwargs):
        raise RuntimeError("fake transaction failure")

    from telebotshop.infrastructure.database.repositories.orders import SqlOrderRepository

    target = (
        SqlAlchemyUnitOfWork
        if stage == "commit"
        else SqlOrderRepository
        if stage == "audit"
        else SqlPaymentRepository
    )
    with monkeypatch.context() as patch:
        patch.setattr(target, stage, fail)
        with pytest.raises(RuntimeError, match="fake transaction failure"):
            await payment_service(engine).record(receipt(order))
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM payments")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 0
        assert await connection.scalar(text("SELECT status FROM orders")) == "AWAITING_PAYMENT"
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "RESERVED"
    assert (await payment_service(engine).record(receipt(order))).outcome == PaymentOutcome.MATCHED


@pytest.mark.asyncio
async def test_commit_constraint_rejects_missing_durable_work(migrated_database, monkeypatch):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())

    async def omit(*args):
        pass

    monkeypatch.setattr(SqlPaymentRepository, "save_work", omit)
    with pytest.raises(IntegrityError):
        await payment_service(engine).record(receipt(order))
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM payments")) == 0
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "RESERVED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE payments SET amount = 1",
        "DELETE FROM payments",
        "TRUNCATE payments CASCADE",
        "UPDATE orders SET status = 'EXPIRED', closed_at = now(), close_reason = 'TTL_EXPIRED'",
        "UPDATE inventory_units SET status = 'AVAILABLE', "
        "allocated_item_id = NULL, allocated_at = NULL",
        "UPDATE fulfillments SET destination_telegram_id = 102",
        "DELETE FROM fulfillments",
        "TRUNCATE fulfillments CASCADE",
    ],
)
async def test_financial_history_paid_state_and_allocation_are_immutable(migrated_database, sql):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payment_service(engine).record(receipt(order))
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(text(sql))


@pytest.mark.asyncio
async def test_two_different_charges_contend_on_one_order_and_credit_only_once(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    payments = payment_service(engine)
    pending = None
    try:
        async with engine.begin() as blocker:
            await blocker.execute(text("SELECT id FROM orders FOR UPDATE"))
            pending = asyncio.gather(
                payments.record(receipt(order, charge="FAKE-a")),
                payments.record(receipt(order, charge="FAKE-b")),
            )
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
        assert {r.outcome for r in results} == {
            PaymentOutcome.MATCHED,
            PaymentOutcome.REQUIRES_REVIEW,
        }
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM payments")) == 2
            assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("payment_first", [False, True])
async def test_payment_vs_expiry_uses_same_order_lock_and_never_loses_receipt(
    migrated_database,
    monkeypatch,
    payment_first,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await historical_order(engine, product)
    locked, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def held_uow():
        async with unit_of_work(engine) as work:
            original = work.payments.mark_paid if payment_first else work.orders.close

            async def hold(*args, **kwargs):
                locked.set()
                await release.wait()
                return await original(*args, **kwargs)

            if payment_first:
                work.payments.mark_paid = hold
            else:
                work.orders.close = hold
            yield work

    from telebotshop.application.services.orders import OrderService

    first = asyncio.create_task(
        PaymentService(held_uow).record(receipt(order))
        if payment_first
        else OrderService(held_uow).expire()
    )
    second = None
    try:
        await asyncio.wait_for(locked.wait(), 1.5)
        second = asyncio.create_task(
            order_service(engine).expire()
            if payment_first
            else payment_service(engine).record(receipt(order))
        )
        if payment_first:
            # Expiry skips the paid transaction's locked row.
            assert await second == 0
        else:
            async with engine.connect() as connection:
                async with asyncio.timeout(1):
                    while not await connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_locks WHERE relation = 'orders'::regclass "
                            "AND mode = 'RowShareLock' AND pid <> pg_backend_pid()"
                        )
                    ):
                        await asyncio.sleep(0.01)
        release.set()
        await first
        if not payment_first:
            assert (await second).outcome == PaymentOutcome.MATCHED
        paid = await order_service(engine).get(101, order.id)
        assert paid.status == OrderStatus.PAID
        assert (paid.attention_reason is None) is payment_first
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM payments")) == 1
            assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == int(
                payment_first
            )
    finally:
        release.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)
