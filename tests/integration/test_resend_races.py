import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.fulfillment import DeliveryRequest
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.resends import RecordingSender, delivered_order, resend_service


@pytest.mark.asyncio
@pytest.mark.parametrize("hold_first", [True, False])
async def test_resend_and_hold_serialize_on_two_physical_connections(migrated_database, hold_first):
    engine = migrated_database
    order, work_id, _ = await delivered_order(engine)
    action = await resend_service(engine, RecordingSender(engine)).prepare(101, order.id)
    acquired, release, attempting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    pids = []

    async def hold(*, first):
        try:
            async with engine.begin() as connection:
                pids.append(await connection.scalar(text("SELECT pg_backend_pid()")))
                if not first:
                    attempting.set()
                await connection.execute(
                    text("SELECT id FROM orders WHERE id=:id FOR UPDATE"), {"id": order.id}
                )
                if first:
                    acquired.set()
                    await release.wait()
                await connection.execute(
                    text("UPDATE fulfillments SET hold=true WHERE id=:id"), {"id": work_id}
                )
            return True
        except DBAPIError:
            return False

    @asynccontextmanager
    async def factory():
        async with unit_of_work(engine) as work:
            pids.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
            original = work.orders.order

            async def get(order_id, *, for_update=False):
                if for_update and hold_first:
                    attempting.set()
                value = await original(order_id, for_update=for_update)
                if for_update and not hold_first:
                    acquired.set()
                    await release.wait()
                return value

            work.orders.order = get
            yield work

    service = resend_service(engine, RecordingSender(engine), factory=factory)
    task1 = asyncio.create_task(hold(first=True) if hold_first else service.begin(101, action.id))
    task2 = None
    try:
        await asyncio.wait_for(acquired.wait(), 3)
        task2 = asyncio.create_task(
            service.begin(101, action.id) if hold_first else hold(first=False)
        )
        await asyncio.wait_for(attempting.wait(), 3)
    finally:
        release.set()
        results = await asyncio.gather(*(t for t in (task1, task2) if t), return_exceptions=True)
    assert len(set(pids)) == 2
    if hold_first:
        assert results[0] is True and isinstance(results[1], InvalidAction)
    else:
        assert isinstance(results[0], DeliveryRequest) and results[1] is False
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM resend_actions")) == (
            "PENDING" if hold_first else "SENDING"
        )
        assert await connection.scalar(text("SELECT hold FROM fulfillments")) is hold_first


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["prepare", "confirm"])
async def test_two_requests_share_one_active_action_and_one_send(migrated_database, phase):
    engine = migrated_database
    order, _, _ = await delivered_order(engine)
    sender = RecordingSender(engine)
    base = resend_service(engine, sender)
    action = await base.prepare(101, order.id) if phase == "confirm" else None
    acquired, release, attempting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    pids = []

    def factory(first):
        @asynccontextmanager
        async def wrapped():
            async with unit_of_work(engine) as work:
                pids.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
                original = work.orders.order

                async def get(order_id, *, for_update=False):
                    if for_update and not first:
                        attempting.set()
                    value = await original(order_id, for_update=for_update)
                    if for_update and first:
                        acquired.set()
                        await release.wait()
                    return value

                work.orders.order = get
                yield work

        return wrapped

    async def run(first):
        service = resend_service(engine, sender, factory=factory(first))
        return (
            await service.prepare(101, order.id)
            if phase == "prepare"
            else await service.begin(101, action.id)
        )

    first = asyncio.create_task(run(True))
    second = None
    try:
        await asyncio.wait_for(acquired.wait(), 3)
        second = asyncio.create_task(run(False))
        await asyncio.wait_for(attempting.wait(), 3)
    finally:
        release.set()
        values = await asyncio.gather(*(t for t in (first, second) if t))
    assert len(set(pids)) == 2
    if phase == "prepare":
        assert values[0].id == values[1].id
    else:
        assert sum(isinstance(value, DeliveryRequest) for value in values) == 1
        assert values[1].status == "SENDING"
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM resend_actions")) == 1
