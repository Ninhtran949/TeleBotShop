import asyncio

from aiogram import Bot
from aiogram.exceptions import (
    ClientDecodeError,
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import LabeledPrice

from telebotshop.application.ports.invoices import InvoiceSender
from telebotshop.domain.models.orders import InvoiceDelivery, InvoiceRequest, InvoiceStatus


class StarsInvoiceSender(InvoiceSender):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(self, request: InvoiceRequest) -> InvoiceDelivery:
        order = request.order
        try:
            async with asyncio.timeout(10):
                message = await self.bot.send_invoice(
                    chat_id=request.buyer_telegram_id,
                    title=order.title[:32],
                    description=(order.description or order.title)[:255],
                    payload=order.payload,
                    currency="XTR",
                    prices=[LabeledPrice(label=order.title[:32], amount=order.price_stars)],
                    provider_token="",
                    start_parameter=f"order_{order.id.hex}",
                    request_timeout=10,
                )
        except (
            TelegramBadRequest,
            TelegramForbiddenError,
            TelegramNotFound,
            TelegramUnauthorizedError,
            TelegramRetryAfter,
        ):
            return InvoiceDelivery(InvoiceStatus.REJECTED)
        except (TelegramAPIError, ClientDecodeError, TimeoutError, OSError):
            return InvoiceDelivery(InvoiceStatus.UNCERTAIN)
        if message.chat.id != request.buyer_telegram_id or message.message_id <= 0:
            return InvoiceDelivery(InvoiceStatus.UNCERTAIN)
        return InvoiceDelivery(InvoiceStatus.SENT, message.message_id)
