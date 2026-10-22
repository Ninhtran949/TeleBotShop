from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from telebotshop.admin.identity import back_keyboard
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.manual import ManualFulfillmentService
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.catalog import Page
from telebotshop.domain.models.manual import ManualDetail, ManualWorkView


def queue_view(page: Page[ManualWorkView]) -> tuple[str, InlineKeyboardMarkup]:
    text = f"Đơn chờ xử lý thủ công · Trang {page.page + 1}\n"
    rows = [
        [
            InlineKeyboardButton(
                text=f"{item.sku} · {str(item.order_id)[:8]}",
                callback_data=f"mf:detail:{item.work_id}",
            )
        ]
        for item in page.items
    ]
    if not rows:
        text += "Chưa có công việc chờ xử lý trong trang này."
    if page.page:
        rows.append(
            [InlineKeyboardButton(text="Trang trước", callback_data=f"mf:queue:{page.page - 1}")]
        )
    if page.has_more:
        rows.append(
            [InlineKeyboardButton(text="Trang sau", callback_data=f"mf:queue:{page.page + 1}")]
        )
    return text, InlineKeyboardMarkup(inline_keyboard=rows + back_keyboard().inline_keyboard)


def detail_text(detail: ManualDetail) -> str:
    return (
        f"Xử lý thủ công · {detail.sku}\nĐơn: {detail.order_id}\n"
        f"Khách (Telegram ID): {detail.destination_telegram_id}\n"
        f"Thông báo chờ: {detail.customer_notice}\n\n"
        f"Hướng dẫn riêng cho staff:\n{detail.staff_instructions}\n\n"
        "Sau khi đã hoàn tất giao hàng, gửi:\n"
        f"/manual_complete {detail.work_id}\n"
        "Bằng chứng hoặc tham chiếu xử lý (tối đa 500 UTF-8 bytes)."
    )


def create_manual_admin_router(manual: ManualFulfillmentService, access: AccessService) -> Router:
    router = Router(name="admin_manual")

    @router.message(Command("manual"))
    async def manual_command(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        await access.authorize(message.from_user.id, Permission.MANAGE_FULFILLMENT)
        if command.args:
            try:
                work_id = UUID(command.args.strip())
            except ValueError:
                raise InvalidInput("Invalid manual work") from None
            text, keyboard = (
                detail_text(await manual.detail(message.from_user.id, work_id)),
                back_keyboard(),
            )
        else:
            text, keyboard = queue_view(await manual.queue(message.from_user.id))
        await message.answer(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.message(Command("manual_complete"))
    async def complete_command(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        await access.authorize(message.from_user.id, Permission.MANAGE_FULFILLMENT)
        reference, _, evidence = (command.args or "").partition("\n")
        try:
            work_id = UUID(reference.strip())
        except ValueError:
            raise InvalidInput(
                "Use /manual_complete WORK_ID followed by evidence on a new line"
            ) from None
        action = await manual.prepare_completion(message.from_user.id, work_id, evidence)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Xác nhận đã giao", callback_data=f"mf:confirm:{action.id}"
                    ),
                    InlineKeyboardButton(text="Hủy", callback_data=f"mf:cancel:{action.id}"),
                ]
            ]
        )
        await message.answer(
            f"Xác nhận hoàn tất công việc {work_id}?\n"
            "Chỉ xác nhận sau khi đã hoàn tất giao hàng thủ công. "
            "Bằng chứng đã được lưu mã hóa; xác nhận hết hạn sau 10 phút.",
            reply_markup=keyboard,
            parse_mode=None,
        )

    @router.callback_query(F.data.startswith("mf:"))
    async def callback(query: CallbackQuery) -> None:
        await access.authorize(query.from_user.id, Permission.MANAGE_FULFILLMENT)
        if not isinstance(query.message, Message) or len(query.data or "") > 64:
            raise InvalidInput("Invalid manual callback")
        try:
            prefix, operation, reference = (query.data or "").split(":")
            if prefix != "mf":
                raise ValueError
            if operation == "queue":
                text, keyboard = queue_view(await manual.queue(query.from_user.id, int(reference)))
            elif operation == "detail":
                text, keyboard = (
                    detail_text(await manual.detail(query.from_user.id, UUID(reference))),
                    back_keyboard(),
                )
            elif operation == "confirm":
                changed = await manual.confirm(query.from_user.id, UUID(reference))
                text, keyboard = (
                    (
                        "Đã ghi nhận giao hàng thủ công."
                        if changed
                        else "Đơn đã được hoàn tất trước đó."
                    ),
                    back_keyboard(),
                )
            elif operation == "cancel":
                await manual.cancel(query.from_user.id, UUID(reference))
                text, keyboard = (
                    "Đã hủy xác nhận; công việc vẫn giữ trạng thái hiện tại.",
                    back_keyboard(),
                )
            else:
                raise ValueError
        except ValueError:
            raise InvalidInput("Invalid manual callback") from None
        await query.answer()
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    return router
