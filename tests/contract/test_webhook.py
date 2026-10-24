import asyncio
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from telebotshop.api import webhook as webhook_module
from telebotshop.api.webhook import create_webhook_router
from telebotshop.infrastructure.telegram.webhook import dispatch_update
from tests.telegram import payment_update

SECRET = "FAKE_test_webhook_secret_1234567890123456"
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": SECRET, "Content-Type": "application/json"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,body,status",
    [
        ({}, b"{}", 403),
        ({**HEADERS, "X-Telegram-Bot-Api-Secret-Token": "wrong"}, b"{}", 403),
        ({**HEADERS, "Content-Type": "text/plain"}, b"{}", 415),
        ({**HEADERS, "Content-Encoding": "gzip"}, b"{}", 415),
        (HEADERS, b"{", 400),
        (HEADERS, b'{"update_id":1,"update_id":2}', 400),
        (HEADERS, b'{"update_id":"invalid"}', 400),
        (HEADERS, b"x" * (256 * 1024 + 1), 413),
    ],
    ids=[
        "missing-secret",
        "wrong-secret",
        "wrong-type",
        "compressed",
        "invalid-json",
        "duplicate-fields",
        "invalid-update",
        "oversize",
    ],
)
async def test_webhook_refuses_unauthenticated_malformed_or_oversize_input(headers, body, status):
    receiver = AsyncMock()
    app = FastAPI()
    app.include_router(create_webhook_router(partial(dispatch_update, receiver), SECRET))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fake-test"
    ) as client:
        response = await client.post("/telegram/webhook", headers=headers, content=body)
    assert response.status_code == status
    receiver.assert_not_awaited()
    assert SECRET not in response.text


@pytest.mark.asyncio
async def test_webhook_awaits_durable_receipt_and_redacts_commit_failure(caplog):
    receiver = AsyncMock(side_effect=SQLAlchemyError("FAKE_SECRET_DATABASE_BODY"))
    app = FastAPI()
    app.include_router(create_webhook_router(partial(dispatch_update, receiver), SECRET))
    update = payment_update()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fake-test"
    ) as client:
        payload = update.model_dump(mode="json", by_alias=True, exclude_none=True)
        failed = await client.post("/telegram/webhook", headers=HEADERS, json=payload)
        assert failed.status_code == 503
        assert "FAKE_SECRET_DATABASE_BODY" not in failed.text + caplog.text
        receiver.side_effect = None
        response = await client.post("/telegram/webhook", headers=HEADERS, json=payload)
        assert response.status_code == 200
        assert receiver.await_count == 2
        assert (
            receiver.await_args.args[0].message.successful_payment.telegram_payment_charge_id
            == "FAKE-charge-1"
        )


@pytest.mark.asyncio
async def test_chunked_body_cap_and_duplicate_secret_headers_reject_before_dispatch():
    receiver = AsyncMock()
    app = FastAPI()
    app.include_router(create_webhook_router(partial(dispatch_update, receiver), SECRET))

    async def chunks():
        yield b"x" * (128 * 1024)
        yield b"y" * (128 * 1024 + 1)

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fake-test"
    ) as client:
        response = await client.post("/telegram/webhook", headers=HEADERS, content=chunks())
        assert response.status_code == 413
        duplicate = [("X-Telegram-Bot-Api-Secret-Token", SECRET)] * 2 + [
            ("Content-Type", "application/json")
        ]
        response = await client.post(
            "/telegram/webhook", headers=duplicate, content=b'{"update_id":1}'
        )
        assert response.status_code == 403
    receiver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["stream", "dispatch"])
async def test_webhook_deadlines_cancel_stalled_input_or_handler_and_release_slot(
    monkeypatch, stage
):
    real_timeout = asyncio.timeout
    monkeypatch.setattr(
        webhook_module,
        "asyncio",
        SimpleNamespace(Semaphore=asyncio.Semaphore, timeout=lambda seconds: real_timeout(0.01)),
    )
    never = asyncio.Event()
    receiver = AsyncMock(side_effect=lambda update: None)

    async def blocked(update):
        await never.wait()

    async def stream():
        yield b'{"update_id":'
        await never.wait()

    if stage == "dispatch":
        receiver.side_effect = blocked
    app = FastAPI()
    app.include_router(create_webhook_router(partial(dispatch_update, receiver), SECRET))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fake-test"
    ) as client:
        async with real_timeout(1):
            if stage == "stream":
                response = await client.post("/telegram/webhook", headers=HEADERS, content=stream())
            else:
                response = await client.post(
                    "/telegram/webhook", headers=HEADERS, json={"update_id": 1}
                )
            assert response.status_code == 503
            receiver.side_effect = None
            assert (
                await client.post("/telegram/webhook", headers=HEADERS, json={"update_id": 2})
            ).status_code == 200


@pytest.mark.asyncio
async def test_webhook_saturation_rejects_excess_and_releases_all_slots():
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def consume(update):
        nonlocal count
        count += 1
        if count == 32:
            entered.set()
        await release.wait()

    app = FastAPI()
    app.include_router(create_webhook_router(partial(dispatch_update, consume), SECRET))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fake-test"
    ) as client:
        tasks = [
            asyncio.create_task(
                client.post("/telegram/webhook", headers=HEADERS, json={"update_id": n})
            )
            for n in range(32)
        ]
        try:
            async with asyncio.timeout(2):
                await entered.wait()
                assert (
                    await client.post("/telegram/webhook", headers=HEADERS, json={"update_id": 33})
                ).status_code == 503
                assert count == 32
        finally:
            release.set()
            responses = await asyncio.gather(*tasks)
        assert all(response.status_code == 200 for response in responses)
        assert (
            await client.post("/telegram/webhook", headers=HEADERS, json={"update_id": 34})
        ).status_code == 200
