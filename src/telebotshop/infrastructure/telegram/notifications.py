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

from telebotshop.application.ports.notifications import NotificationSender
from telebotshop.domain.models.notifications import (
    NotificationKind,
    NotificationRequest,
    NotificationResult,
    NotificationState,
)

MESSAGES = {
    NotificationKind.INVOICE_UNCERTAIN: (
        "Hóa đơn chưa rõ kết quả; kiểm tra trước khi tạo hóa đơn khác",
        "/staff_orders",
    ),
    NotificationKind.PAYMENT_REVIEW: ("Có receipt thanh toán cần đối soát", "/refunds"),
    NotificationKind.PAID_ATTENTION: ("Đơn đã thanh toán cần xử lý entitlement", "/staff_orders"),
    NotificationKind.DELIVERY_ATTENTION: (
        "Giao hàng thất bại hoặc chưa rõ kết quả; không gửi lại mù",
        "/staff_orders",
    ),
    NotificationKind.MANUAL_WAITING: ("Đơn thủ công đang chờ xử lý", "/manual"),
    NotificationKind.BACKLOG: (
        "Work giao hàng đã chờ quá 5 phút; kiểm tra worker",
        "/staff_orders",
    ),
    NotificationKind.LOW_STOCK: ("Có sản phẩm sắp hết hàng; mở /lowstock để xem", None),
    NotificationKind.REFUND_UNCERTAIN: (
        "Refund chưa rõ kết quả; cần đối soát, không hoàn lại lần nữa",
        "/reconcile_refund",
    ),
    NotificationKind.TICKET_ACTIVITY: ("Ticket đang mở có cập nhật từ khách", "/staff_tickets"),
    NotificationKind.TICKET_REPLY: ("Ticket của bạn có phản hồi; mở thread để đọc", "/ticket"),
    NotificationKind.WALLET_TOPUP: (
        "Yêu cầu nạp đã được đối soát và cộng credit; mở /wallet để xem số dư",
        None,
    ),
    NotificationKind.TOPUP_REVIEW: (
        "Có top-up cần đối soát hoặc cảnh báo trùng bằng chứng",
        "/wallet_reviews",
    ),
}


class TelegramNotificationSender(NotificationSender):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(self, request: NotificationRequest) -> NotificationResult:
        remaining = (request.deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return NotificationResult(NotificationState.FAILED)
        text, command = MESSAGES[request.kind]
        text += f"\nMã tham chiếu: {request.target_id}"
        if command:
            text += f"\n{command} {request.target_id}"
        timeout = min(10, remaining)
        try:
            async with asyncio.timeout(timeout):
                message = await self.bot.send_message(
                    chat_id=request.telegram_id,
                    text=text,
                    parse_mode=None,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    request_timeout=timeout,
                )
        except (
            TelegramBadRequest,
            TelegramForbiddenError,
            TelegramNotFound,
            TelegramUnauthorizedError,
            TelegramRetryAfter,
        ):
            return NotificationResult(NotificationState.FAILED)
        except (TelegramAPIError, ClientDecodeError, OSError, TimeoutError):
            return NotificationResult(NotificationState.UNCERTAIN)
        if (
            message.chat.id != request.telegram_id
            or message.chat.type != "private"
            or message.message_id <= 0
        ):
            return NotificationResult(NotificationState.UNCERTAIN)
        return NotificationResult(NotificationState.SENT, message.message_id)
