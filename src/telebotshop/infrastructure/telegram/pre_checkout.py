import asyncio
import logging

from aiogram import Router
from aiogram.exceptions import ClientDecodeError, TelegramAPIError
from aiogram.types import PreCheckoutQuery
from sqlalchemy.exc import SQLAlchemyError

from telebotshop.application.ports.pre_checkout import PRE_CHECKOUT_DB_BUDGET, PreCheckoutCheck
from telebotshop.domain.errors import InvalidAction, InvalidInput
from telebotshop.domain.models.pre_checkout import PreCheckout

logger = logging.getLogger(__name__)
PRE_CHECKOUT_API_BUDGET = 3
REJECTION_TEXT = "Chưa thể xác nhận thanh toán. Vui lòng kiểm tra đơn hoặc liên hệ hỗ trợ."


def create_pre_checkout_router(check: PreCheckoutCheck) -> Router:
    router = Router(name="pre_checkout")

    @router.pre_checkout_query()
    async def pre_checkout(query: PreCheckoutQuery) -> None:
        approved = False
        try:
            async with asyncio.timeout(PRE_CHECKOUT_DB_BUDGET):
                if not query.from_user.is_bot:
                    request = PreCheckout(
                        query.id,
                        query.from_user.id,
                        query.currency,
                        query.total_amount,
                        query.invoice_payload,
                    )
                    approved = await check(request)
        except (InvalidInput, InvalidAction):
            pass
        except (SQLAlchemyError, TimeoutError, OSError):
            logger.warning("pre_checkout_validation_unavailable")
        try:
            async with asyncio.timeout(PRE_CHECKOUT_API_BUDGET):
                confirmed = await query.bot.answer_pre_checkout_query(
                    pre_checkout_query_id=query.id,
                    ok=approved,
                    error_message=None if approved else REJECTION_TEXT,
                    request_timeout=PRE_CHECKOUT_API_BUDGET,
                )
                if not confirmed:
                    logger.warning("pre_checkout_answer_unconfirmed")
        except (TelegramAPIError, ClientDecodeError, TimeoutError, OSError):
            # An answer may have reached Telegram; keep the committed query binding.
            logger.warning("pre_checkout_answer_unconfirmed")

    return router
