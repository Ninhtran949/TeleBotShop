import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.infrastructure.database.uow import unit_of_work


@pytest.mark.asyncio
async def test_uow_requires_explicit_commit_and_rolls_back_failures(database: AsyncEngine) -> None:
    async with database.begin() as connection:
        await connection.execute(text("CREATE TABLE uow_probe (value integer PRIMARY KEY)"))
    async with unit_of_work(database) as uow:
        await uow.session.execute(text("INSERT INTO uow_probe VALUES (1)"))
    with pytest.raises(ValueError, match="rollback"):
        async with unit_of_work(database) as uow:
            await uow.session.execute(text("INSERT INTO uow_probe VALUES (2)"))
            raise ValueError("rollback")
    async with unit_of_work(database) as uow:
        await uow.session.execute(text("INSERT INTO uow_probe VALUES (3)"))
        await uow.commit()
        with pytest.raises(InvalidRequestError):
            await uow.session.execute(text("INSERT INTO uow_probe VALUES (4)"))
    async with database.connect() as connection:
        assert (await connection.scalars(text("SELECT value FROM uow_probe"))).all() == [3]
    assert database.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_entire_transaction(database: AsyncEngine) -> None:
    async with database.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE uow_probe (value integer, "
                "CONSTRAINT uq_probe UNIQUE (value) DEFERRABLE INITIALLY DEFERRED)"
            )
        )
    with pytest.raises(IntegrityError):
        async with unit_of_work(database) as uow:
            await uow.session.execute(text("INSERT INTO uow_probe VALUES (1), (1)"))
            await uow.commit()
    async with database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM uow_probe")) == 0
    assert database.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_cancellation_rolls_back_and_closes_session(database: AsyncEngine) -> None:
    async with database.begin() as connection:
        await connection.execute(text("CREATE TABLE uow_probe (value integer PRIMARY KEY)"))
    started = asyncio.Event()

    async def write_without_finishing() -> None:
        async with unit_of_work(database) as uow:
            await uow.session.execute(text("INSERT INTO uow_probe VALUES (1)"))
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(write_without_finishing())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM uow_probe")) == 0
    assert database.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_concurrent_uows_use_independent_transactions(database: AsyncEngine) -> None:
    async with database.begin() as connection:
        await connection.execute(text("CREATE TABLE uow_probe (value integer PRIMARY KEY)"))
    barrier = asyncio.Barrier(2)

    async def write(value: int, commit: bool) -> int:
        async with unit_of_work(database) as uow:
            backend = await uow.session.scalar(text("SELECT pg_backend_pid()"))
            await uow.session.execute(
                text("INSERT INTO uow_probe VALUES (:value)"), {"value": value}
            )
            await barrier.wait()
            if commit:
                await uow.commit()
            return backend

    backends = await asyncio.wait_for(asyncio.gather(write(1, True), write(2, False)), timeout=5)
    assert len(set(backends)) == 2
    async with database.connect() as connection:
        assert (await connection.scalars(text("SELECT value FROM uow_probe"))).all() == [1]
    assert database.pool.checkedout() == 0
