from collections.abc import AsyncGenerator
from contextlib import aclosing

from aiogram import Bot, Dispatcher, F, Router
from aiogram.dispatcher.dispatcher import DEFAULT_BACKOFF_CONFIG
from aiogram.types import Message, Update
from aiogram.utils.backoff import BackoffConfig

from telebotshop.application.ports.payments import PaymentReceiver
from telebotshop.application.ports.refunds import RefundReceiver
from telebotshop.domain.models.payments import PaymentReceipt
from telebotshop.infrastructure.telegram.refunds import refund_from_message


def receipt_from_message(message: Message) -> PaymentReceipt:
    payment = message.successful_payment
    assert payment is not None
    expiry = payment.subscription_expiration_date
    return PaymentReceipt(
        payment.telegram_payment_charge_id,
        message.from_user.id if message.from_user is not None else None,
        payment.currency,
        payment.total_amount,
        payment.invoice_payload,
        payment.provider_payment_charge_id,
        payment.is_recurring or False,
        payment.is_first_recurring or False,
        expiry,
        payment.order_info is not None,
        payment.shipping_option_id,
    )


def create_payment_router(record: PaymentReceiver, refunded: RefundReceiver) -> Router:
    router = Router(name="payments")

    @router.message(F.successful_payment | F.refunded_payment)
    async def financial_receipt(message: Message):
        result = None
        if message.successful_payment is not None:
            result = await record(receipt_from_message(message))
        if message.refunded_payment is not None:
            result = await refunded(refund_from_message(message))
        return result

    return router


class PaymentsDispatcher(Dispatcher):
    def __init__(self, record: PaymentReceiver, refunded: RefundReceiver) -> None:
        super().__init__(disable_fsm=True)
        self.record_payment = record
        self.record_refund = refunded

    async def _listen_updates(
        self,
        bot: Bot,
        polling_timeout: int = 30,
        backoff_config: BackoffConfig = DEFAULT_BACKOFF_CONFIG,
        allowed_updates: list[str] | None = None,
    ) -> AsyncGenerator[Update, None]:
        # shortcut: aiogram 3.31 advances offset after yield; recheck this hook on SDK upgrades.
        async with aclosing(
            super()._listen_updates(
                bot,
                polling_timeout,
                backoff_config,
                allowed_updates,
            )
        ) as updates:
            async for update in updates:
                if update.message is not None and update.message.successful_payment is not None:
                    await self.record_payment(receipt_from_message(update.message))
                if update.message is not None and update.message.refunded_payment is not None:
                    await self.record_refund(refund_from_message(update.message))
                yield update
