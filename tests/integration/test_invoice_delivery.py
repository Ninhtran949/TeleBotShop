import asyncio
from datetime import timedelta
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telebotshop.application.services.invoices import InvoiceService
from telebotshop.domain.errors import AccessDenied
from telebotshop.domain.models.orders import InvoiceDelivery, InvoiceStatus, OrderStatus
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.orders import historical_order, order_service, setup_orders


class FakeSender:
    def __init__(self, engine, outcome=InvoiceStatus.SENT):
        self.engine, self.outcome, self.calls = engine, outcome, []

    async def send(self, request):
        assert self.engine.pool.checkedout() == 0
        async with self.engine.connect() as connection:
            assert await connection.scalar(text("SELECT status FROM invoice_intents")) == "SENDING"
        self.calls.append(request)
        return InvoiceDelivery(self.outcome, 20 if self.outcome == InvoiceStatus.SENT else None)


def invoice_service(engine, sender):
    return InvoiceService(partial(unit_of_work, engine), sender)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", [InvoiceStatus.SENT, InvoiceStatus.REJECTED, InvoiceStatus.UNCERTAIN]
)
async def test_invoice_claim_is_durable_network_is_outside_transaction_and_never_retried(
    migrated_database,
    outcome,
):
    engine = migrated_database
    product = await setup_orders(engine)
    orders = order_service(engine)
    order = await orders.create(101, product.id, uuid4())
    sender = FakeSender(engine, outcome)
    invoices = invoice_service(engine, sender)
    result = await invoices.send(101, order.id)
    assert result.invoice_status == outcome
    assert len(sender.calls) == 1
    assert sender.calls[0].order.price_stars == 100 and sender.calls[0].buyer_telegram_id == 101
    assert await invoices.send(101, order.id) == result
    assert len(sender.calls) == 1
    assert result.status == (
        OrderStatus.EXPIRED if outcome == InvoiceStatus.REJECTED else OrderStatus.AWAITING_PAYMENT
    )
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == (
            "AVAILABLE" if outcome == InvoiceStatus.REJECTED else "RESERVED"
        )


@pytest.mark.asyncio
async def test_wrong_buyer_and_expired_order_never_send(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    old = await historical_order(engine, product)
    sender = FakeSender(engine)
    invoices = invoice_service(engine, sender)
    with pytest.raises(AccessDenied):
        await invoices.send(102, old.id)
    result = await invoices.send(101, old.id)
    assert result.status == OrderStatus.EXPIRED and result.invoice_status == InvoiceStatus.CANCELLED
    assert sender.calls == []


@pytest.mark.asyncio
async def test_concurrent_send_claims_only_one_attempt_and_cancel_never_revives_order(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine)
    orders = order_service(engine)
    order = await orders.create(101, product.id, uuid4())
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class WaitingSender:
        async def send(self, request):
            assert engine.pool.checkedout() == 0
            calls.append(request)
            entered.set()
            await release.wait()
            return InvoiceDelivery(InvoiceStatus.SENT, 30)

    invoices = invoice_service(engine, WaitingSender())
    pending = asyncio.create_task(invoices.send(101, order.id))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        assert (await invoices.send(101, order.id)).invoice_status == InvoiceStatus.SENDING
        await orders.cancel(101, order.id)
    finally:
        release.set()
    result = await pending
    assert result.invoice_status == InvoiceStatus.SENT and result.status == OrderStatus.EXPIRED
    assert len(calls) == 1
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "AVAILABLE"


@pytest.mark.asyncio
async def test_cancelled_network_task_keeps_claim_and_does_not_blindly_resend(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())

    class InterruptedSender:
        calls = 0

        async def send(self, request):
            self.calls += 1
            raise asyncio.CancelledError

    sender = InterruptedSender()
    invoices = invoice_service(engine, sender)
    with pytest.raises(asyncio.CancelledError):
        await invoices.send(101, order.id)
    assert (await invoices.send(101, order.id)).invoice_status == InvoiceStatus.SENDING
    assert sender.calls == 1
    assert await order_service(engine).recover_invoices() == 0


@pytest.mark.asyncio
async def test_post_send_commit_failure_keeps_claim_and_cannot_create_second_invoice(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())

    class CommitFailureSender(FakeSender):
        async def send(self, request):
            result = await super().send(request)
            async with engine.begin() as connection:
                await connection.execute(
                    text("""CREATE FUNCTION fail_invoice_audit() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN
                      IF NEW.action = 'invoice.sent' THEN
                        RAISE EXCEPTION 'injected post-send failure';
                      END IF; RETURN NEW;
                    END; $$""")
                )
                await connection.execute(
                    text("""CREATE CONSTRAINT TRIGGER fail_invoice_commit
                    AFTER INSERT ON audit_logs DEFERRABLE INITIALLY DEFERRED
                    FOR EACH ROW EXECUTE FUNCTION fail_invoice_audit()""")
                )
            return result

    sender = CommitFailureSender(engine)
    invoices = invoice_service(engine, sender)
    with pytest.raises(DBAPIError):
        await invoices.send(101, order.id)
    assert (await invoices.send(101, order.id)).invoice_status == InvoiceStatus.SENDING
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_expiry_can_run_while_invoice_network_call_is_in_flight(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await historical_order(
        engine, product, age=timedelta(minutes=15) - timedelta(seconds=2)
    )
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowSender:
        async def send(self, request):
            assert engine.pool.checkedout() == 0
            entered.set()
            await release.wait()
            return InvoiceDelivery(InvoiceStatus.SENT, 20)

    pending = asyncio.create_task(invoice_service(engine, SlowSender()).send(101, order.id))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        await asyncio.sleep(2.1)
        assert await order_service(engine).expire() == 1
    finally:
        release.set()
    result = await pending
    assert result.status == OrderStatus.EXPIRED and result.invoice_status == InvoiceStatus.SENT


@pytest.mark.asyncio
async def test_stale_claim_recovery_is_bounded_concurrent_and_never_resends(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine, 2)
    old = [await historical_order(engine, product, age=timedelta(minutes=1)) for _ in range(2)]
    async with engine.begin() as connection:
        await connection.execute(
            text("""UPDATE invoice_intents
            SET status = 'SENDING', attempt_id = gen_random_uuid(),
                started_at = clock_timestamp() - interval '31 seconds'""")
        )
    orders = order_service(engine)
    counts = await asyncio.gather(orders.recover_invoices(1), orders.recover_invoices(1))
    assert sum(counts) == 2
    assert await orders.recover_invoices() == 0
    sender = FakeSender(engine)
    for order in old:
        result = await invoice_service(engine, sender).send(101, order.id)
        assert result.invoice_status == InvoiceStatus.UNCERTAIN
        assert result.status == OrderStatus.AWAITING_PAYMENT
    assert sender.calls == []
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM inventory_units WHERE status = 'RESERVED'")
            )
            == 2
        )


@pytest.mark.asyncio
async def test_known_rejection_after_cancel_does_not_release_another_order(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    orders = order_service(engine)
    first = await orders.create(101, product.id, uuid4())
    second = None

    class ReallocatingSender:
        async def send(self, request):
            nonlocal second
            assert engine.pool.checkedout() == 0
            await orders.cancel(101, first.id)
            second = await orders.create(102, product.id, uuid4())
            return InvoiceDelivery(InvoiceStatus.REJECTED)

    result = await invoice_service(engine, ReallocatingSender()).send(101, first.id)
    assert result.status == OrderStatus.EXPIRED and result.invoice_status == InvoiceStatus.REJECTED
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT reservation_item_id FROM inventory_units WHERE status = 'RESERVED'")
            )
            == second.item_id
        )


@pytest.mark.asyncio
async def test_db_only_expiry_entrypoint_runs_both_batches_in_owned_schema(
    migrated_database,
    monkeypatch,
):
    from telebotshop import bootstrap
    from telebotshop.infrastructure.config import Settings

    engine = migrated_database
    product = await setup_orders(engine)
    await historical_order(engine, product)
    async with engine.begin() as connection:
        await connection.execute(
            text("""UPDATE invoice_intents SET status = 'SENDING',
            attempt_id = gen_random_uuid(),
            started_at = clock_timestamp() - interval '31 seconds'""")
        )
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(bootstrap, "Bot", lambda *args: pytest.fail("No Telegram in maintenance"))
    assert await bootstrap.run_expiry(
        Settings(
            app_env="test",
            database_url="postgresql+asyncpg://fake:fake@127.0.0.1:55433/fake",
        )
    ) == (1, 1)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "AVAILABLE"
        assert await connection.scalar(text("SELECT status FROM invoice_intents")) == "UNCERTAIN"
