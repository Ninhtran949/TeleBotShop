import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from telebotshop.application.services.notifications import NotificationService
from telebotshop.domain.models.inventory import ImportFormat
from telebotshop.domain.models.notifications import (
    NotificationRequest,
    NotificationResult,
    NotificationState,
)
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import timed_uow
from tests.inventory import inventory_service
from tests.orders import setup_orders


class Sender:
    def __init__(self, engine):
        self.engine, self.requests = engine, []

    async def send(self, request):
        assert self.engine.pool.checkedout() == 0
        self.requests.append(request)
        return NotificationResult(NotificationState.SENT, 123)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["restock", "owner-revoked"])
async def test_stale_or_unauthorized_notification_is_suppressed_before_network(
    migrated_database, change
):
    engine = migrated_database
    await setup_orders(engine)
    sender = Sender(engine)
    service = NotificationService(partial(unit_of_work, engine), sender, threshold=1)
    assert await service.discover() == 1
    if change == "restock":
        inventory = inventory_service(engine)
        action = await inventory.prepare_import(
            9001, "DEMO-1", b"FAKE-new-1\nFAKE-new-2", ImportFormat.TXT
        )
        await inventory.confirm(9001, action.id)
    else:
        async with engine.begin() as c:
            await c.execute(text("DELETE FROM user_roles WHERE role='OWNER'"))
    assert await service.run_once()
    assert not sender.requests
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM notifications")) == "SUPPRESSED"


@pytest.mark.asyncio
async def test_expired_notification_retains_uncertainty_and_one_late_observation(migrated_database):
    engine = migrated_database
    await setup_orders(engine)
    sender = Sender(engine)
    service = NotificationService(partial(unit_of_work, engine), sender, threshold=5)
    await service.discover()
    async with unit_of_work(engine) as work:
        note = await work.notifications.next()
        now = await work.orders.now()
        deadline = await work.notifications.start(note, now - timedelta(minutes=2))
        await work.commit()
    assert not await service.run_once()
    request = NotificationRequest(note.id, note.kind, note.target_id, 9001, deadline)
    await service.complete(request, NotificationResult(NotificationState.SENT, 888))
    await service.complete(request, NotificationResult(NotificationState.FAILED))
    assert not await service.run_once() and not sender.requests
    async with engine.connect() as c:
        row = (
            await c.execute(text("SELECT state,observed_state,message_id FROM notifications"))
        ).one()
        assert tuple(row) == ("UNCERTAIN", "SENT", 888)


@pytest.mark.asyncio
async def test_two_physical_workers_skip_locked_notification_and_send_once(migrated_database):
    engine = migrated_database
    await setup_orders(engine)
    sender = Sender(engine)
    entered, release = asyncio.Event(), asyncio.Event()
    pids = set()

    def factory(block):
        @asynccontextmanager
        async def create():
            async with unit_of_work(engine) as work:
                pids.add(await work.session.scalar(text("SELECT pg_backend_pid()")))
                original = work.notifications.start

                async def start(note, now):
                    deadline = await original(note, now)
                    entered.set()
                    await release.wait()
                    return deadline

                if block:
                    work.notifications.start = start
                yield work

        return create

    initial = NotificationService(partial(unit_of_work, engine), sender, threshold=5)
    await initial.discover()
    first = NotificationService(factory(True), sender, threshold=5)
    second = NotificationService(factory(False), sender, threshold=5)
    task = asyncio.create_task(first.run_once())
    try:
        async with asyncio.timeout(3):
            await entered.wait()
            assert not await second.run_once()
        assert len(pids) == 2
    finally:
        release.set()
        await task
    assert len(sender.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("position", [1, 2], ids=["begin", "complete"])
@pytest.mark.parametrize("lost_ack", [False, True], ids=["rollback", "lost-ack"])
async def test_notification_commit_failure_never_blindly_reissues(
    migrated_database, position, lost_ack
):
    engine = migrated_database
    await setup_orders(engine)
    sender = Sender(engine)
    normal = NotificationService(partial(unit_of_work, engine), sender, threshold=5)
    await normal.discover()
    calls = 0

    @asynccontextmanager
    async def faulty():
        nonlocal calls
        async with unit_of_work(engine) as work:
            original = work.commit

            async def commit():
                nonlocal calls
                calls += 1
                if calls == position and not lost_ack:
                    raise SQLAlchemyError("FAKE before commit")
                await original()
                if calls == position and lost_ack:
                    raise SQLAlchemyError("FAKE lost acknowledgement")

            work.commit = commit
            yield work

    faulty_service = NotificationService(faulty, sender, threshold=5)
    with pytest.raises(SQLAlchemyError):
        await faulty_service.run_once()
    if position == 1 and not lost_ack:
        assert await normal.run_once()
        assert len(sender.requests) == 1
        expected = "SENT"
    else:
        assert not await normal.run_once()
        async with engine.connect() as c:
            now = await c.scalar(text("SELECT clock_timestamp()"))
        future = NotificationService(
            timed_uow(engine, now + timedelta(minutes=2)), sender, threshold=5
        )
        assert not await future.run_once()
        expected = "SENT" if position == 2 and lost_ack else "UNCERTAIN"
        assert len(sender.requests) == (1 if position == 2 else 0)
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM notifications")) == expected
