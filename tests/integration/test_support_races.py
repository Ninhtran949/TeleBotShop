import asyncio
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.support import TicketKind
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import paid_work
from tests.support import support_service


@pytest.mark.asyncio
@pytest.mark.parametrize("close_first", [True, False])
async def test_close_and_reply_share_ticket_lock_on_two_physical_connections(
    migrated_database, close_first
):
    engine = migrated_database
    await paid_work(engine)
    service = support_service(engine)
    ticket = await service.create(101, TicketKind.GENERAL, "FAKE question", uuid4())
    action = await service.prepare_close(101, ticket.id)
    acquired, attempting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    pids = []

    def factory(first):
        @asynccontextmanager
        async def wrapped():
            async with unit_of_work(engine) as work:
                pids.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
                original = work.support.get

                async def get(ticket_id, *, for_update=False):
                    if for_update and not first:
                        attempting.set()
                    value = await original(ticket_id, for_update=for_update)
                    if for_update and first:
                        acquired.set()
                        await release.wait()
                    return value

                work.support.get = get
                yield work

        return wrapped

    async def run(first):
        current = support_service(engine, factory=factory(first))
        if first == close_first:
            return await current.confirm(101, action.id)
        return await current.reply(101, ticket.id, "FAKE racing reply", uuid4())

    one = asyncio.create_task(run(True))
    two = None
    try:
        await asyncio.wait_for(acquired.wait(), 3)
        two = asyncio.create_task(run(False))
        await asyncio.wait_for(attempting.wait(), 3)
    finally:
        release.set()
        results = await asyncio.gather(
            *(task for task in (one, two) if task), return_exceptions=True
        )
    assert len(set(pids)) == 2
    assert results[0] is True if close_first else isinstance(results[0], UUID)
    assert isinstance(results[1], InvalidAction)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM support_tickets")) == (
            "CLOSED" if close_first else "OPEN"
        )
        assert await connection.scalar(text("SELECT count(*) FROM support_messages")) == (
            1 if close_first else 2
        )


@pytest.mark.asyncio
async def test_duplicate_creation_reply_and_close_do_not_duplicate_history(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    service = support_service(engine)
    request = uuid4()
    a, b = await asyncio.gather(
        *(service.create(101, TicketKind.GENERAL, "FAKE duplicate", request) for _ in range(2))
    )
    assert a.id == b.id
    request = uuid4()
    a_id, b_id = await asyncio.gather(
        *(service.reply(101, a.id, "FAKE reply", request) for _ in range(2))
    )
    assert a_id == b_id
    close = await service.prepare_close(101, a.id)
    assert sorted(await asyncio.gather(*(service.confirm(101, close.id) for _ in range(2)))) == [
        False,
        True,
    ]
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM support_tickets")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM support_messages")) == 2
