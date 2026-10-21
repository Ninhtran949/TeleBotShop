import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from telebotshop.application.services.fulfillment import FulfillmentService
from telebotshop.domain.models.fulfillment import DeliveryResult, DeliveryState
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import fulfillment_service, paid_work


@pytest.mark.asyncio
async def test_two_physical_worker_connections_skip_locked_claim_and_send_only_once(
    migrated_database,
):
    engine = migrated_database
    await paid_work(engine)
    locked, release = asyncio.Event(), asyncio.Event()
    pids = set()
    sender = AsyncMock()
    sender.send.return_value = DeliveryResult(DeliveryState.SENT, message_id=22)
    service = fulfillment_service(engine, sender)

    def factory(hold):
        @asynccontextmanager
        async def uow():
            async with unit_of_work(engine) as work:
                original = work.fulfillment.claimable

                async def claimable(now):
                    pids.add(await work.session.scalar(text("SELECT pg_backend_pid()")))
                    result = await original(now)
                    if hold:
                        locked.set()
                        await release.wait()
                    return result

                work.fulfillment.claimable = claimable
                yield work

        return uow

    first = asyncio.create_task(
        FulfillmentService(factory(True), service.cipher, sender, service.policy).run_once()
    )
    try:
        await asyncio.wait_for(locked.wait(), 1.5)
        second = FulfillmentService(factory(False), service.cipher, sender, service.policy)
        assert await second.run_once() is False
        assert len(pids) == 2
        release.set()
        assert await first is True
        sender.send.assert_awaited_once()
    finally:
        release.set()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_hold_after_claim_prevents_decrypt_and_new_claim(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    sender = AsyncMock()
    service = fulfillment_service(engine, sender)
    service.cipher = Mock(wraps=service.cipher)
    claim = await service.claim()
    preparing = None
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT id FROM orders FOR UPDATE"))
            await connection.execute(text("UPDATE fulfillments SET hold = true"))
            preparing = asyncio.create_task(service.prepare(claim))
            async with asyncio.timeout(1.5):
                while not await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE relation = 'orders'::regclass "
                        "AND mode = 'RowShareLock' AND pid <> pg_backend_pid()"
                    )
                ):
                    await asyncio.sleep(0.01)
            assert not preparing.done()
        assert await preparing is None
    finally:
        if preparing is not None:
            preparing.cancel()
            await asyncio.gather(preparing, return_exceptions=True)
    assert await service.claim() is None
    service.cipher.open.assert_not_called()
    sender.send.assert_not_awaited()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "READY"
        assert await connection.scalar(text("SELECT state FROM delivery_attempts")) == "ABANDONED"


@pytest.mark.asyncio
async def test_prepare_wins_order_lock_and_inflight_hold_is_rejected(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    service = fulfillment_service(engine, AsyncMock())
    claim = await service.claim()
    locked, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def held_uow():
        async with unit_of_work(engine) as work:
            original = work.fulfillment.start_send

            async def start_send(*args):
                locked.set()
                await release.wait()
                return await original(*args)

            work.fulfillment.start_send = start_send
            yield work

    async def hold():
        async with engine.begin() as connection:
            await connection.execute(text("SELECT id FROM orders FOR UPDATE"))
            await connection.execute(text("UPDATE fulfillments SET hold = true"))

    preparing = asyncio.create_task(
        FulfillmentService(held_uow, service.cipher, service.sender, service.policy).prepare(claim)
    )
    holding = None
    try:
        await asyncio.wait_for(locked.wait(), 1.5)
        holding = asyncio.create_task(hold())
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
        assert await preparing is not None
        with pytest.raises(IntegrityError):
            await holding
    finally:
        release.set()
        for task in (preparing, holding):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (preparing, holding) if t is not None), return_exceptions=True
        )
