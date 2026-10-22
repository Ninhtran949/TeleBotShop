import asyncio
from contextlib import asynccontextmanager
from copy import copy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.refunds import RefundRequest, RefundResult, RefundState
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import fulfillment_service, paid_work
from tests.manual import manual_service
from tests.refunds import refund_service
from tests.resends import RecordingSender, delivered_order, resend_service


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", ["worker", "resend", "manual"])
@pytest.mark.parametrize("refund_first", [True, False])
async def test_refund_serializes_with_worker_resend_and_manual_on_two_physical_connections(
    migrated_database, flow, refund_first
):
    engine = migrated_database
    if flow == "worker":
        order, _ = await paid_work(engine)
        other = fulfillment_service(engine, AsyncMock())
        token = await other.claim()

        async def operate(service):
            return await service.prepare(token)
    else:
        order, work_id, _ = await delivered_order(
            engine, ProductType.MANUAL_FULFILLMENT if flow == "manual" else ProductType.UNIQUE_KEY
        )
        other = (
            manual_service(engine)
            if flow == "manual"
            else resend_service(engine, RecordingSender(engine))
        )
        if flow == "manual":
            token = await other.prepare_completion(9001, work_id, "FAKE-completion-evidence")

            async def operate(service):
                return await service.confirm(9001, token.id)
        else:
            token = await other.prepare(101, order.id)

            async def operate(service):
                return await service.begin(101, token.id)

    refunds = refund_service(engine)
    async with engine.connect() as connection:
        payment = await connection.scalar(text("SELECT id FROM payments"))
    action = await refunds.prepare(9001, payment, "FAKE-refund-policy-decision")
    acquired, attempting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    pids = []

    def factory(first):
        @asynccontextmanager
        async def wrapped():
            async with unit_of_work(engine) as work:
                pids.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
                original = work.orders.order

                async def lock(order_id, *, for_update=False):
                    if for_update and not first:
                        attempting.set()
                    result = await original(order_id, for_update=for_update)
                    if for_update and first:
                        acquired.set()
                        await release.wait()
                    return result

                work.orders.order = lock
                yield work

        return wrapped

    async def run(first):
        current = copy(refunds if first == refund_first else other)
        current.uow = factory(first)
        return (
            await current.begin(9001, action.id)
            if first == refund_first
            else await operate(current)
        )

    one = asyncio.create_task(run(True))
    two = None
    try:
        await asyncio.wait_for(acquired.wait(), 3)
        two = asyncio.create_task(run(False))
        await asyncio.wait_for(attempting.wait(), 3)
    finally:
        release.set()
        results = await asyncio.gather(
            *(task for task in [one, two] if task), return_exceptions=True
        )
    assert len(set(pids)) == 2
    if refund_first:
        assert isinstance(results[0], RefundRequest)
        assert results[1] is None if flow == "worker" else isinstance(results[1], InvalidAction)
        await refunds.complete(action.id, RefundResult(RefundState.SUCCEEDED))
    else:
        assert not isinstance(results[0], Exception)
        assert isinstance(results[1], InvalidAction)
    refunds.gateway.refund.assert_not_awaited()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM inventory_units LIMIT 1")) in {
            "ALLOCATED",
            None,
        }
        assert await connection.scalar(text("SELECT hold FROM fulfillments")) is refund_first
        assert await connection.scalar(text("SELECT status FROM refund_intents")) == (
            "SUCCEEDED" if refund_first else "PENDING"
        )


@pytest.mark.asyncio
async def test_duplicate_confirmations_commit_one_intent_before_one_refund_call(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    service = refund_service(engine)
    async with engine.connect() as connection:
        payment = await connection.scalar(text("SELECT id FROM payments"))
    action = await service.prepare(9001, payment, "FAKE-reviewed")
    acquired, attempting, release, sending, finish = (asyncio.Event() for _ in range(5))
    pids = []

    def factory(first):
        @asynccontextmanager
        async def wrapped():
            async with unit_of_work(engine) as work:
                pids.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
                original = work.payments.lock_charge

                async def lock(charge):
                    if not first:
                        attempting.set()
                    await original(charge)
                    if first:
                        acquired.set()
                        await release.wait()

                work.payments.lock_charge = lock
                yield work

        return wrapped

    async def refund(request):
        sending.set()
        await finish.wait()
        return RefundResult(RefundState.SUCCEEDED)

    service.gateway.refund.side_effect = refund
    first, second = copy(service), copy(service)
    first.uow, second.uow = factory(True), factory(False)
    one = asyncio.create_task(first.confirm(9001, action.id))
    two = None
    try:
        await asyncio.wait_for(acquired.wait(), 3)
        two = asyncio.create_task(second.confirm(9001, action.id))
        await asyncio.wait_for(attempting.wait(), 3)
        release.set()
        assert (await asyncio.wait_for(two, 3)).status == RefundState.SENDING
        await asyncio.wait_for(sending.wait(), 3)
        finish.set()
        assert (await one).status == RefundState.SUCCEEDED
    finally:
        release.set()
        finish.set()
        await asyncio.gather(*(task for task in [one, two] if task), return_exceptions=True)
    assert len(set(pids[:2])) == 2
    service.gateway.refund.assert_awaited_once()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_intents")) == 1
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
