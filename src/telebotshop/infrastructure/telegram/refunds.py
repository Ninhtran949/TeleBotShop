import asyncio
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import Message, StarTransactions, TransactionPartnerUser

from telebotshop.application.ports.refunds import RefundGateway
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.refunds import (
    RefundReceipt,
    RefundRequest,
    RefundResult,
    RefundState,
    StarEvidence,
)


def refund_from_message(message: Message) -> RefundReceipt:
    payment = message.refunded_payment
    assert payment is not None
    return RefundReceipt(
        payment.telegram_payment_charge_id,
        payment.currency,
        payment.total_amount,
        payment.invoice_payload,
        payment.provider_payment_charge_id or "",
    )


class TelegramRefundGateway(RefundGateway):
    def __init__(self, bot: Bot):
        self.bot = bot

    async def refund(self, request: RefundRequest) -> RefundResult:
        remaining = (request.deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return RefundResult(RefundState.FAILED)
        try:
            async with asyncio.timeout(min(remaining, request.timeout_seconds)):
                result = await self.bot.refund_star_payment(
                    user_id=request.payer_id,
                    telegram_payment_charge_id=request.charge_id,
                    request_timeout=request.timeout_seconds,
                )
        except TelegramRetryAfter:
            return RefundResult(RefundState.FAILED)
        except (TelegramAPIError, OSError, TimeoutError):
            return RefundResult(RefundState.UNCERTAIN)
        return RefundResult(RefundState.SUCCEEDED if result is True else RefundState.UNCERTAIN)

    async def transactions(self, offset: int) -> tuple[StarEvidence, ...]:
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise InvalidInput("Invalid transaction offset")
        async with asyncio.timeout(10):
            result = await self.bot.get_star_transactions(
                offset=offset, limit=100, request_timeout=10
            )
        if not isinstance(result, StarTransactions) or len(result.transactions) > 100:
            raise InvalidInput("Invalid transaction evidence")
        evidence = []
        for item in result.transactions:
            if (item.source is None) == (item.receiver is None):
                continue
            partner = item.source if item.source is not None else item.receiver
            if (
                not isinstance(partner, TransactionPartnerUser)
                or partner.transaction_type != "invoice_payment"
                or item.nanostar_amount not in {None, 0}
                or type(item.amount) is not int
                or item.amount == 0
                or partner.user.is_bot
            ):
                continue
            evidence.append(
                StarEvidence(
                    item.id,
                    partner.user.id,
                    abs(item.amount),
                    partner.invoice_payload,
                    item.receiver is not None,
                    item.date,
                )
            )
        return tuple(evidence)
