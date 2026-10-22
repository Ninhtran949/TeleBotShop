from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendDocument, SendMessage

from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import DeliveryRequest, DeliveryState, ManualNotice
from telebotshop.domain.models.resources import ResourceContext
from telebotshop.infrastructure.delivery.finite import FiniteDeliverySender
from tests.resources import resource_payload
from tests.telegram import TelegramRecorder


@pytest.mark.asyncio
async def test_document_ack_without_a_document_is_uncertain(monkeypatch):
    from aiogram.types import Chat, Message

    telegram = TelegramRecorder(monkeypatch)
    telegram.bot.session.make_request.return_value = Message(
        message_id=99, date=1, chat=Chat(id=101, type="private"), text="Unexpected text ACK"
    )
    telegram.bot.session.make_request.side_effect = None
    request = DeliveryRequest(
        uuid4(),
        uuid4(),
        101,
        ResourceContext(uuid4(), "DEMO-1", uuid4(), ProductType.DIGITAL_FILE),
        resource_payload(ProductType.DIGITAL_FILE),
        datetime.now(UTC) + timedelta(seconds=60),
        10,
    )
    try:
        assert (
            await FiniteDeliverySender(telegram.bot).send(request)
        ).state == DeliveryState.UNCERTAIN
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,document",
    [
        (ProductType.DIGITAL_FILE, False),
        (ProductType.REUSABLE_RESOURCE, False),
        (ProductType.REUSABLE_RESOURCE, True),
        (ProductType.MANUAL_FULFILLMENT, False),
    ],
)
async def test_typed_resource_transport_never_sends_staff_notes_or_fetches_urls(
    monkeypatch, kind, document
):
    telegram = TelegramRecorder(monkeypatch)
    payload = resource_payload(kind, document=document)
    if kind == ProductType.MANUAL_FULFILLMENT:
        payload = ManualNotice(payload.customer_notice)
    request = DeliveryRequest(
        uuid4(),
        uuid4(),
        101,
        ResourceContext(uuid4(), "DEMO-1", uuid4(), kind),
        payload,
        datetime.now(UTC) + timedelta(seconds=60),
        10,
    )
    try:
        result = await FiniteDeliverySender(telegram.bot).send(request)
        assert result.state == DeliveryState.SENT
        assert len(telegram.calls) == 1
        sent = telegram.calls[0]
        assert sent.chat_id == 101 and sent.parse_mode is None
        if kind == ProductType.DIGITAL_FILE or document:
            assert isinstance(sent, SendDocument) and sent.document == payload.file_id
        else:
            assert isinstance(sent, SendMessage)
            assert sent.text == (
                payload.customer_notice if isinstance(payload, ManualNotice) else payload.text
            )
            assert sent.link_preview_options.is_disabled is True
        assert "private staff instructions" not in str(sent)
    finally:
        await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,state",
    [
        ("reject", DeliveryState.FAILED),
        ("rate", DeliveryState.RETRYABLE),
        ("network", DeliveryState.UNCERTAIN),
    ],
)
async def test_document_errors_keep_same_known_or_uncertain_policy_without_text_fallback(
    monkeypatch, caplog, failure, state
):
    telegram = TelegramRecorder(monkeypatch)
    calls = []

    async def send(bot, method, timeout=None):
        calls.append(method)
        if failure == "reject":
            raise TelegramBadRequest(method, "FAKE sensitive API error")
        if failure == "rate":
            raise TelegramRetryAfter(method, "FAKE sensitive API error", retry_after=90)
        raise TelegramNetworkError(method, "FAKE sensitive API error")

    telegram.bot.session.make_request.side_effect = send
    request = DeliveryRequest(
        uuid4(),
        uuid4(),
        101,
        ResourceContext(uuid4(), "DEMO-1", uuid4(), ProductType.DIGITAL_FILE),
        resource_payload(ProductType.DIGITAL_FILE),
        datetime.now(UTC) + timedelta(seconds=60),
        10,
    )
    try:
        result = await FiniteDeliverySender(telegram.bot).send(request)
        assert result.state == state and len(calls) == 1 and isinstance(calls[0], SendDocument)
        assert "sensitive" not in caplog.text + repr(result)
    finally:
        await telegram.bot.session.close()
