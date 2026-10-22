import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text

from telebotshop.application.services.manual import ManualFulfillmentService
from telebotshop.domain.errors import InvalidAction
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.inventory import fake_cipher
from tests.manual import manual_service, waiting_manual


@pytest.mark.asyncio
@pytest.mark.parametrize("hold_first", [True, False])
async def test_manual_completion_and_hold_use_same_order_lock(migrated_database, hold_first):
    engine = migrated_database
    order, work_id = await waiting_manual(engine)
    action = await manual_service(engine).prepare_completion(9001, work_id, "FAKE evidence")
    acquired, release, attempting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    connections = []

    async def hold(*, first):
        async with engine.begin() as connection:
            connections.append(await connection.scalar(text("SELECT pg_backend_pid()")))
            if not first:
                attempting.set()
            await connection.execute(
                text("SELECT id FROM orders WHERE id = :id FOR UPDATE"), {"id": order.id}
            )
            if first:
                acquired.set()
                await release.wait()
            state = await connection.scalar(
                text("SELECT state FROM fulfillments WHERE id = :id"), {"id": work_id}
            )
            if state != "AWAITING_MANUAL":
                return False
            await connection.execute(
                text("UPDATE fulfillments SET hold = true WHERE id = :id"), {"id": work_id}
            )
            return True

    @asynccontextmanager
    async def factory():
        async with unit_of_work(engine) as work:
            connections.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
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

    service = ManualFulfillmentService(factory, fake_cipher())
    task1 = asyncio.create_task(
        hold(first=True) if hold_first else service.confirm(9001, action.id)
    )
    task2 = None
    try:
        await asyncio.wait_for(acquired.wait(), 2)
        task2 = asyncio.create_task(
            service.confirm(9001, action.id) if hold_first else hold(first=False)
        )
        await asyncio.wait_for(attempting.wait(), 2)
    finally:
        release.set()
        results = await asyncio.gather(
            *(task for task in (task1, task2) if task is not None), return_exceptions=True
        )
    assert len(set(connections)) == 2
    assert results[0] is True
    if hold_first:
        assert isinstance(results[1], InvalidAction)
    else:
        assert results[1] is False
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == (
            "AWAITING_MANUAL" if hold_first else "SENT"
        )
        assert await connection.scalar(text("SELECT status FROM orders")) == (
            "FULFILLING" if hold_first else "DELIVERED"
        )
