from functools import partial
from time import monotonic
from uuid import uuid4

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import TelegramNetworkError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.telegram.pre_checkout import create_pre_checkout_router
from tests.orders import order_service, setup_orders
from tests.pre_checkout import payable_invoice
from tests.telegram import TelegramRecorder, pre_checkout_update


def dispatcher(engine):
    result = Dispatcher(disable_fsm=True)
    validator = PreCheckoutService(partial(unit_of_work, engine))
    result.include_router(create_pre_checkout_router(validator.check))
    return result


@pytest.mark.asyncio
async def test_answer_after_commit_and_lost_answer_never_clears_approval(
    migrated_database, monkeypatch
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payable_invoice(engine, order)
    recorder = TelegramRecorder(monkeypatch)
    calls = []

    async def send(bot, method, timeout=None):
        assert engine.pool.checkedout() == 0
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT query_id FROM pre_checkout_approvals"))
                == "fake_query"
            )
        calls.append(method)
        if method.ok:
            raise TelegramNetworkError(method, "FAKE-private-body")
        return True

    recorder.bot.session.make_request.side_effect = send
    router = dispatcher(engine)
    try:
        await router.feed_update(recorder.bot, pre_checkout_update(payload=order.payload))
        assert len(calls) == 1 and calls[0].ok is True
        await router.feed_update(
            recorder.bot,
            pre_checkout_update(
                payload=order.payload,
                query_id="another_query",
            ),
        )
        assert len(calls) == 2 and calls[1].ok is False
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM pre_checkout_approvals")) == 1
            assert await connection.scalar(text("SELECT status FROM orders")) == "AWAITING_PAYMENT"
    finally:
        await recorder.bot.session.close()


@pytest.mark.asyncio
async def test_order_lock_contention_returns_safe_answer_within_deadline(
    migrated_database, monkeypatch, caplog
):
    engine = migrated_database
    product = await setup_orders(engine)
    order = await order_service(engine).create(101, product.id, uuid4())
    await payable_invoice(engine, order)
    recorder = TelegramRecorder(monkeypatch)
    try:
        async with engine.begin() as blocker:
            await blocker.execute(text("SELECT id FROM orders FOR UPDATE"))
            started = monotonic()
            await dispatcher(engine).feed_update(
                recorder.bot, pre_checkout_update(payload=order.payload)
            )
            assert monotonic() - started < 8
            assert len(recorder.calls) == 1 and recorder.calls[0].ok is False
            assert engine.pool.checkedout() == 1  # Only the test's blocker remains.
            assert order.payload not in caplog.text
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM pre_checkout_approvals")) == 0
    finally:
        await recorder.bot.session.close()


@pytest.mark.asyncio
async def test_connection_pool_wait_has_total_budget_before_answer(migrated_database, monkeypatch):
    async with migrated_database.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    limited = create_async_engine(
        migrated_database.url,
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        connect_args={"timeout": 2, "server_settings": {"search_path": schema}},
    )
    recorder = TelegramRecorder(monkeypatch)
    try:
        async with limited.begin() as blocker:
            await blocker.execute(text("SELECT 1"))
            started = monotonic()
            await dispatcher(limited).feed_update(recorder.bot, pre_checkout_update())
            elapsed = monotonic() - started
            assert 3.5 <= elapsed < 8
            assert len(recorder.calls) == 1 and recorder.calls[0].ok is False
            assert limited.pool.checkedout() == 1
        assert limited.pool.checkedout() == 0
    finally:
        await recorder.bot.session.close()
        await limited.dispose()
