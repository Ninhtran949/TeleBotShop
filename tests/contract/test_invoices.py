from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from aiogram.exceptions import (
    ClientDecodeError,
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.methods import SendInvoice

from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.orders import (
    RESERVATION_TTL,
    InvoiceRequest,
    InvoiceStatus,
    Order,
    OrderStatus,
)
from telebotshop.infrastructure.telegram.invoices import StarsInvoiceSender
from tests.telegram import TelegramRecorder


def fake_request():
    now = datetime.now(UTC)
    order = Order(
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        "FAKE",
        ProductType.LICENSE,
        "Tên sản phẩm " * 5,
        "Mô tả " * 100,
        123,
        1,
        now,
        now + RESERVATION_TTL,
        OrderStatus.AWAITING_PAYMENT,
        InvoiceStatus.SENDING,
        "x" * 32,
        uuid4(),
        now,
    )
    return InvoiceRequest(order, 101, order.attempt_id)


@pytest.mark.asyncio
async def test_stars_invoice_has_server_price_private_buyer_and_no_forwarded_pay(monkeypatch):
    recorder = TelegramRecorder(monkeypatch)
    try:
        request = fake_request()
        result = await StarsInvoiceSender(recorder.bot).send(request)
        assert result.status == InvoiceStatus.SENT and result.message_id == 20
        method = recorder.calls[0]
        assert isinstance(method, SendInvoice)
        assert method.chat_id == 101
        assert method.currency == "XTR" and method.provider_token == ""
        assert len(method.prices) == 1 and method.prices[0].amount == 123
        assert method.title == request.order.title[:32]
        assert method.description == request.order.description[:255]
        assert method.payload == request.order.payload
        assert method.start_parameter == f"order_{request.order.id.hex}"
        assert not method.need_name and not method.need_email and not method.need_phone_number
        assert not method.need_shipping_address and not method.is_flexible
        assert recorder.bot.session.make_request.await_args.kwargs["timeout"] == 10
    finally:
        await recorder.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (TelegramBadRequest, InvoiceStatus.REJECTED),
        (TelegramForbiddenError, InvoiceStatus.REJECTED),
        (TelegramNotFound, InvoiceStatus.REJECTED),
        (TelegramUnauthorizedError, InvoiceStatus.REJECTED),
        (TelegramRetryAfter, InvoiceStatus.REJECTED),
        (TelegramNetworkError, InvoiceStatus.UNCERTAIN),
        (TelegramServerError, InvoiceStatus.UNCERTAIN),
        (TelegramAPIError, InvoiceStatus.UNCERTAIN),
        (ClientDecodeError, InvoiceStatus.UNCERTAIN),
        (TimeoutError, InvoiceStatus.UNCERTAIN),
        (OSError, InvoiceStatus.UNCERTAIN),
    ],
)
async def test_invoice_outcomes_do_not_log_or_return_sdk_payload(
    monkeypatch,
    caplog,
    exception,
    expected,
):
    recorder = TelegramRecorder(monkeypatch)
    request = fake_request()
    method = SendInvoice(
        chat_id=101, title="Fake", description="Fake", payload="fake", currency="XTR", prices=[]
    )
    secret = "FAKE-sensitive-payload"
    if exception == TelegramRetryAfter:
        failure = exception(method, secret, retry_after=10)
    elif exception == ClientDecodeError:
        failure = exception(secret, ValueError(secret), {"payload": secret})
    elif issubclass(exception, TelegramAPIError):
        failure = exception(method, secret)
    else:
        failure = exception(secret)
    recorder.bot.session.make_request.side_effect = failure
    try:
        result = await StarsInvoiceSender(recorder.bot).send(request)
        assert result.status == expected and result.message_id is None
        assert secret not in repr(result) + caplog.text
    finally:
        await recorder.bot.session.close()


@pytest.mark.asyncio
async def test_empty_description_falls_back_to_title(monkeypatch):
    recorder = TelegramRecorder(monkeypatch)
    try:
        request = fake_request()
        request = replace(request, order=replace(request.order, description=""))
        await StarsInvoiceSender(recorder.bot).send(request)
        assert recorder.calls[0].description == request.order.title[:255]
    finally:
        await recorder.bot.session.close()
