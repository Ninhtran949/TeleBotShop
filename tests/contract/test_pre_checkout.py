import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import ClientDecodeError, TelegramNetworkError
from aiogram.filters import Command
from aiogram.methods import AnswerPreCheckoutQuery
from aiogram.types import Message, User
from sqlalchemy.exc import OperationalError

from telebotshop.bootstrap import create_dispatcher
from telebotshop.domain.models.pre_checkout import PreCheckout
from telebotshop.infrastructure.telegram import pre_checkout as boundary
from telebotshop.infrastructure.telegram.pre_checkout import create_pre_checkout_router
from tests.telegram import TelegramRecorder, message_update, pre_checkout_update


@pytest.mark.asyncio
async def test_runtime_pre_checkout_uses_service_with_durable_receipt_intake(monkeypatch):
    recorder = TelegramRecorder(monkeypatch)
    try:
        check = AsyncMock(return_value=True)
        dispatcher = create_dispatcher(
            Mock(), Mock(), Mock(), Mock(), Mock(check=check), AsyncMock()
        )
        await dispatcher.feed_update(recorder.bot, pre_checkout_update())
        assert len(recorder.calls) == 1
        answer = recorder.calls[0]
        assert isinstance(answer, AnswerPreCheckoutQuery)
        assert answer.ok is True and not answer.error_message
        check.assert_awaited_once()
        assert "pre_checkout_query" in dispatcher.resolve_used_update_types()
    finally:
        await recorder.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_adapter_uses_query_buyer_and_answers_exactly_once(monkeypatch, approved):
    recorder = TelegramRecorder(monkeypatch)
    check = AsyncMock(return_value=approved)
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(create_pre_checkout_router(check))
    try:
        await dispatcher.feed_update(recorder.bot, pre_checkout_update())
        check.assert_awaited_once_with(PreCheckout("fake_query", 101, "XTR", 100, "x" * 32))
        answer = recorder.calls[0]
        assert len(recorder.calls) == 1 and isinstance(answer, AnswerPreCheckoutQuery)
        assert answer.pre_checkout_query_id == "fake_query" and answer.ok is approved
        assert bool(answer.error_message) is not approved
        assert recorder.bot.session.make_request.await_args.kwargs["timeout"] == 3
    finally:
        await recorder.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("actor", 0),
        ("currency", "USD"),
        ("amount", 0),
        ("payload", "forged"),
        ("query_id", "bad\nquery"),
        ("is_bot", True),
    ],
)
async def test_invalid_query_is_rejected_before_business_io(monkeypatch, field, value):
    recorder = TelegramRecorder(monkeypatch)
    check = AsyncMock()
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(create_pre_checkout_router(check))
    try:
        await dispatcher.feed_update(recorder.bot, pre_checkout_update(**{field: value}))
        check.assert_not_awaited()
        assert len(recorder.calls) == 1 and recorder.calls[0].ok is False
    finally:
        await recorder.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["database", "timeout", "network", "decode"])
async def test_failures_return_safe_reject_or_keep_answer_uncertain_without_retry(
    monkeypatch,
    caplog,
    failure,
):
    recorder = TelegramRecorder(monkeypatch)
    secret = "FAKE-private-DSN-payload"
    check = AsyncMock(return_value=True)
    calls = []
    if failure == "database":
        check.side_effect = OperationalError("query", {}, OSError(secret))
    else:

        async def send(bot, method, timeout=None):
            calls.append(method)
            if failure == "timeout":
                await asyncio.sleep(1)
            if failure == "network":
                raise TelegramNetworkError(method, secret)
            raise ClientDecodeError(secret, ValueError(secret), {"payload": secret})

        recorder.bot.session.make_request.side_effect = send
        monkeypatch.setattr(boundary, "PRE_CHECKOUT_API_BUDGET", 0.03)
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(create_pre_checkout_router(check))
    try:
        await dispatcher.feed_update(recorder.bot, pre_checkout_update())
        if failure == "database":
            assert len(recorder.calls) == 1 and recorder.calls[0].ok is False
        else:
            assert len(calls) == 1 and calls[0].ok is True
        assert secret not in caplog.text
        assert secret not in "".join(str(call) for call in recorder.calls + calls)
    finally:
        await recorder.bot.session.close()


@pytest.mark.asyncio
async def test_validation_timeout_leaves_budget_for_safe_answer(monkeypatch):
    recorder = TelegramRecorder(monkeypatch)
    finished = asyncio.Event()

    async def blocked(request):
        try:
            await asyncio.sleep(1)
        finally:
            finished.set()

    monkeypatch.setattr(boundary, "PRE_CHECKOUT_DB_BUDGET", 0.03)
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(create_pre_checkout_router(blocked))
    try:
        await dispatcher.feed_update(recorder.bot, pre_checkout_update())
        assert finished.is_set()
        assert len(recorder.calls) == 1 and recorder.calls[0].ok is False
    finally:
        await recorder.bot.session.close()


@pytest.mark.asyncio
async def test_native_concurrent_dispatch_answers_while_another_update_waits(monkeypatch):
    recorder = TelegramRecorder(monkeypatch)
    dispatcher = create_dispatcher(Mock(), Mock(), Mock(), Mock(), AsyncMock(), AsyncMock())
    started, release, answered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    @dispatcher.message(Command("slow"))
    async def slow(_message: Message):
        started.set()
        await release.wait()

    async def updates(*args, **kwargs):
        yield message_update("/slow")
        await started.wait()
        yield pre_checkout_update()
        await answered.wait()
        release.set()

    async def send(bot, method, timeout=None):
        assert isinstance(method, AnswerPreCheckoutQuery)
        assert started.is_set() and not release.is_set()
        answered.set()
        return True

    monkeypatch.setattr(Dispatcher, "_listen_updates", updates)
    monkeypatch.setattr(
        recorder.bot,
        "me",
        AsyncMock(
            return_value=User(
                id=recorder.bot.id,
                is_bot=True,
                first_name="Fake bot",
            )
        ),
    )
    recorder.bot.session.make_request.side_effect = send
    try:
        await asyncio.wait_for(
            dispatcher._polling(
                recorder.bot,
                handle_as_tasks=True,
                tasks_concurrency_limit=32,
                allowed_updates=["message", "pre_checkout_query"],
            ),
            3,
        )
        assert answered.is_set()
    finally:
        release.set()
        await asyncio.gather(*tuple(dispatcher._handle_update_tasks), return_exceptions=True)
        await recorder.bot.session.close()
