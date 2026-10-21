from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from aiogram.exceptions import (
    ClientDecodeError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message

from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import DeliveryCode, DeliveryRequest, DeliveryState
from telebotshop.domain.models.inventory import InventoryContext, LicensePayload, UniqueKeyPayload
from telebotshop.infrastructure.delivery.finite import FiniteDeliverySender
from tests.telegram import TelegramRecorder


def request(kind=ProductType.UNIQUE_KEY, *, deadline=None):
    payload = (
        UniqueKeyPayload("FAKE-private<&key>")
        if kind == ProductType.UNIQUE_KEY
        else LicensePayload("FAKE-license<&>")
    )
    return DeliveryRequest(
        uuid4(),
        uuid4(),
        101,
        InventoryContext(uuid4(), "DEMO-1", uuid4(), kind),
        payload,
        datetime.now(UTC) + timedelta(seconds=60) if deadline is None else deadline,
        10,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [ProductType.UNIQUE_KEY, ProductType.LICENSE])
async def test_delivery_sends_exact_material_private_plain_text_without_preview(monkeypatch, kind):
    telegram = TelegramRecorder(monkeypatch)
    delivery = request(kind)
    try:
        result = await FiniteDeliverySender(telegram.bot).send(delivery)
        assert result.state == DeliveryState.SENT and result.message_id == 20
        sent = telegram.calls[0]
        assert isinstance(sent, SendMessage) and sent.chat_id == 101 and sent.parse_mode is None
        assert sent.link_preview_options.is_disabled is True
        assert sent.text == (
            delivery.payload.key if kind == ProductType.UNIQUE_KEY else delivery.payload.license_key
        )
        assert telegram.bot.session.make_request.await_args.kwargs["timeout"] == 10
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,expected",
    [
        ("rate", DeliveryState.RETRYABLE),
        ("reject", DeliveryState.FAILED),
        ("network", DeliveryState.UNCERTAIN),
        ("decode", DeliveryState.UNCERTAIN),
        ("wrong_chat", DeliveryState.UNCERTAIN),
    ],
)
async def test_delivery_classifies_response_without_retry_or_secret_exposure(
    monkeypatch, caplog, failure, expected
):
    telegram = TelegramRecorder(monkeypatch)
    calls = []

    async def send(bot, method, timeout=None):
        calls.append(method)
        if failure == "rate":
            raise TelegramRetryAfter(method, "FAKE-secret-body", retry_after=123)
        if failure == "reject":
            raise TelegramBadRequest(method, "FAKE-secret-body")
        if failure == "network":
            raise TelegramNetworkError(method, "FAKE-secret-body")
        if failure == "decode":
            raise ClientDecodeError(
                "FAKE-secret-body", ValueError("fake"), {"body": "FAKE-secret-body"}
            )
        return Message(message_id=7, date=1, chat=Chat(id=102, type="private"))

    telegram.bot.session.make_request.side_effect = send
    try:
        result = await FiniteDeliverySender(telegram.bot).send(request())
        assert result.state == expected and len(calls) == 1
        if failure == "rate":
            assert result.retry_after == 123 and result.code == DeliveryCode.RATE_LIMIT
        assert "FAKE-secret-body" not in repr(result) + caplog.text
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_expired_send_deadline_never_calls_telegram(monkeypatch):
    telegram = TelegramRecorder(monkeypatch)
    try:
        result = await FiniteDeliverySender(telegram.bot).send(
            request(deadline=datetime.now(UTC) - timedelta(seconds=1))
        )
        assert result.state == DeliveryState.UNCERTAIN and result.code == DeliveryCode.LEASE_EXPIRED
        assert not telegram.calls
    finally:
        await telegram.bot.session.close()
