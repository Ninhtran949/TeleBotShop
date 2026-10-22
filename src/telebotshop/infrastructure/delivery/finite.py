import asyncio
from datetime import UTC, datetime

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
from aiogram.types import LinkPreviewOptions

from telebotshop.application.ports.fulfillment import DeliverySender
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.fulfillment import (
    DeliveryCode,
    DeliveryRequest,
    DeliveryResult,
    DeliveryState,
    ManualNotice,
)
from telebotshop.domain.models.inventory import LicensePayload, UniqueKeyPayload
from telebotshop.domain.models.resources import (
    DigitalFilePayload,
    ReusableDocumentPayload,
    ReusableTextPayload,
)


class FiniteDeliverySender(DeliverySender):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(self, request: DeliveryRequest) -> DeliveryResult:
        remaining = (request.deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return DeliveryResult(DeliveryState.UNCERTAIN, code=DeliveryCode.LEASE_EXPIRED)
        if request.context.kind == ProductType.UNIQUE_KEY and isinstance(
            request.payload, UniqueKeyPayload
        ):
            text = request.payload.key
        elif request.context.kind == ProductType.LICENSE and isinstance(
            request.payload, LicensePayload
        ):
            text = request.payload.license_key
        elif request.context.kind == ProductType.REUSABLE_RESOURCE and isinstance(
            request.payload, ReusableTextPayload
        ):
            text = request.payload.text
        elif request.context.kind == ProductType.MANUAL_FULFILLMENT and isinstance(
            request.payload, ManualNotice
        ):
            text = request.payload.customer_notice
        elif (
            request.context.kind == ProductType.DIGITAL_FILE
            and isinstance(request.payload, DigitalFilePayload)
            or request.context.kind == ProductType.REUSABLE_RESOURCE
            and isinstance(request.payload, ReusableDocumentPayload)
        ):
            text = None
        else:
            return DeliveryResult(DeliveryState.FAILED, code=DeliveryCode.INVALID_ENTITLEMENT)
        timeout = min(request.timeout_seconds, remaining)
        try:
            async with asyncio.timeout(timeout):
                if text is None:
                    message = await self.bot.send_document(
                        chat_id=request.destination_telegram_id,
                        document=request.payload.file_id,
                        parse_mode=None,
                        request_timeout=timeout,
                    )
                else:
                    message = await self.bot.send_message(
                        chat_id=request.destination_telegram_id,
                        text=text,
                        parse_mode=None,
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                        request_timeout=timeout,
                    )
        except TelegramRetryAfter as error:
            if type(error.retry_after) is int and 1 <= error.retry_after <= 86400:
                return DeliveryResult(
                    DeliveryState.RETRYABLE,
                    code=DeliveryCode.RATE_LIMIT,
                    retry_after=error.retry_after,
                )
            return DeliveryResult(DeliveryState.FAILED, code=DeliveryCode.REJECTED)
        except (
            TelegramBadRequest,
            TelegramForbiddenError,
            TelegramNotFound,
            TelegramUnauthorizedError,
        ):
            return DeliveryResult(DeliveryState.FAILED, code=DeliveryCode.REJECTED)
        except (TelegramAPIError, ClientDecodeError, TimeoutError, OSError):
            return DeliveryResult(DeliveryState.UNCERTAIN, code=DeliveryCode.NETWORK)
        if (
            message.chat.id != request.destination_telegram_id
            or message.chat.type != "private"
            or message.message_id <= 0
            or text is None
            and message.document is None
        ):
            return DeliveryResult(DeliveryState.UNCERTAIN, code=DeliveryCode.NETWORK)
        return DeliveryResult(DeliveryState.SENT, message_id=message.message_id)
