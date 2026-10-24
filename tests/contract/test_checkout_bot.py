from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest
from aiogram import Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText

from telebotshop.application.services.checkout import CheckoutService
from telebotshop.bot.routers.checkout import create_checkout_router
from telebotshop.domain.models.orders import InvoiceStatus
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.contract.test_invoices import fake_request
from tests.telegram import TelegramRecorder, callback_update


@pytest.mark.asyncio
async def test_checkout_previews_actual_price_requires_consent_and_acks_before_invoice(monkeypatch):
    service = AsyncMock(spec=CheckoutService)
    service.terms_version = "a" * 24
    order = replace(fake_request().order, invoice_status=InvoiceStatus.READY)
    service.prepare.return_value = order
    telegram = TelegramRecorder(monkeypatch)

    async def pay(actor, identifier, version):
        assert isinstance(telegram.calls[-1], AnswerCallbackQuery)
        assert (actor, identifier, version) == (101, order.id, service.terms_version)
        return replace(order, invoice_status=InvoiceStatus.UNCERTAIN)

    service.pay.side_effect = pay
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.callback_query.outer_middleware(IngressGuard())
    dispatcher.include_router(create_checkout_router(service))
    product = uuid4()
    try:
        await dispatcher.feed_update(
            telegram.bot, callback_update(f"buy:{product.hex}:abcdef0123456789")
        )
        service.prepare.assert_awaited_once_with(
            101, product, uuid5(product, "101:abcdef0123456789")
        )
        preview = next(c for c in reversed(telegram.calls) if isinstance(c, EditMessageText))
        assert str(order.id) in preview.text and "123 Stars" in preview.text
        assert order.payload not in preview.text
        assert preview.parse_mode is None and preview.link_preview_options.is_disabled
        buttons = [b for row in preview.reply_markup.inline_keyboard for b in row]
        confirm = next(b.callback_data for b in buttons if b.callback_data.startswith("ck:p:"))
        assert all(len(b.callback_data.encode()) <= 64 for b in buttons)
        assert any(b.callback_data == "sp:terms:0" for b in buttons)
        await dispatcher.feed_update(telegram.bot, callback_update(confirm))
        status = next(c for c in reversed(telegram.calls) if isinstance(c, EditMessageText))
        assert "chưa rõ" in status.text and "không thanh toán lại" in status.text
        assert not any(
            b.callback_data.startswith("ck:p:")
            for row in status.reply_markup.inline_keyboard
            for b in row
        )
    finally:
        await telegram.bot.session.close()
