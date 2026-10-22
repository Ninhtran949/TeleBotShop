import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.application.services.orders import OrderService
from telebotshop.application.services.resources import ResourceService
from telebotshop.domain.errors import InvalidAction
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.inventory import fake_cipher
from tests.payments import payment_service, receipt
from tests.resources import resource_payload, resource_product, resource_service


def product_gate(engine, *, acquired=None, release=None, attempting=None, connections=None):
    @asynccontextmanager
    async def factory():
        async with unit_of_work(engine) as work:
            if connections is not None:
                connections.append(await work.session.scalar(text("SELECT pg_backend_pid()")))
            original = work.catalog.product

            async def product(product_id, *, for_update=False):
                if for_update and attempting is not None:
                    attempting.set()
                result = await original(product_id, for_update=for_update)
                if for_update and acquired is not None:
                    acquired.set()
                    await release.wait()
                return result

            work.catalog.product = product
            yield work

    return factory


@pytest.mark.asyncio
async def test_competing_resource_publications_apply_one_expected_version(migrated_database):
    engine = migrated_database
    product, first = await resource_product(engine)
    service = resource_service(engine)
    candidates = [
        await service.prepare(9001, "DEMO-1", resource_payload(product.draft.product_type))
        for _ in range(2)
    ]
    results = await asyncio.gather(
        *(service.confirm(9001, action.id) for action in candidates), return_exceptions=True
    )
    assert sum(value is True for value in results) == 1
    assert sum(isinstance(value, InvalidAction) for value in results) == 1
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM resource_versions WHERE state = 'ACTIVE'")
            )
            == 1
        )
        assert (
            await connection.scalar(
                text("SELECT state FROM resource_versions WHERE id = :id"), {"id": first.id}
            )
            == "RETIRED"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("checkout_first", [True, False])
async def test_checkout_and_resource_publication_serialize_and_pin_the_winning_snapshot(
    migrated_database, checkout_first
):
    engine = migrated_database
    product, first = await resource_product(engine)
    replacement = await resource_service(engine).prepare(
        9001, "DEMO-1", resource_payload(product.draft.product_type)
    )
    acquired, release, attempting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    connections = []
    winner = product_gate(engine, acquired=acquired, release=release, connections=connections)
    loser = product_gate(engine, attempting=attempting, connections=connections)

    async def checkout(factory):
        return await OrderService(factory).create(101, product.id, uuid4())

    async def publish(factory):
        return await ResourceService(factory, fake_cipher()).confirm(9001, replacement.id)

    task1 = asyncio.create_task(checkout(winner) if checkout_first else publish(winner))
    task2 = None
    try:
        await asyncio.wait_for(acquired.wait(), 2)
        task2 = asyncio.create_task(publish(loser) if checkout_first else checkout(loser))
        await asyncio.wait_for(attempting.wait(), 2)
    finally:
        release.set()
        outcomes = await asyncio.gather(*(task for task in (task1, task2) if task is not None))
    assert len(set(connections)) == 2
    order = outcomes[0 if checkout_first else 1]
    await payment_service(engine).record(receipt(order))
    async with engine.connect() as connection:
        expected = first.id if checkout_first else replacement.id
        assert await connection.scalar(text("SELECT resource_id FROM order_resources")) == expected
        assert await connection.scalar(text("SELECT resource_id FROM fulfillments")) == expected
        assert await connection.scalar(text("SELECT count(*) FROM inventory_units")) == 0
