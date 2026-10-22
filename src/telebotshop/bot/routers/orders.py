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

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.history import HistoryService
from telebotshop.application.services.resends import ResendService
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.access import Permission, can
from telebotshop.domain.models.catalog import Page, validate_page
from telebotshop.domain.models.history import OrderView, ResendAction

STATUS_LABELS = {
    "AWAITING_PAYMENT": "Chờ thanh toán",
    "EXPIRED": "Hết hạn / đã đóng",
    "PAID": "Đã thanh toán",
    "FULFILLING": "Đang giao",
    "DELIVERED": "Đã giao",
    "FAILED": "Cần xử lý giao hàng",
    "REFUNDED": "Đã hoàn tiền",
}
RESEND_LABELS = {
    "PENDING": "Chờ xác nhận gửi lại",
    "SENDING": "Đang gửi lại; không gửi thêm yêu cầu",
    "SENT": "Đã gửi lại cùng tài nguyên tới khách mua",
    "FAILED": "Gửi lại thất bại; tài nguyên và lần giao gốc vẫn được giữ",
    "UNCERTAIN": "Có thể đã gửi lại. Cần staff đối soát; không tự gửi tiếp",
    "CANCELLED": "Xác nhận đã hủy hoặc hết hạn; chưa gửi lại",
}


def navigation(staff=False) -> list[list[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton(
                text="Danh sách đơn", callback_data="ord:a:0" if staff else "ord:l:0"
            )
        ],
        [InlineKeyboardButton(text="Trang chủ", callback_data="home")],
    ]


def page_view(page: Page[OrderView], staff: bool):
    mode, detail = ("a", "s") if staff else ("l", "v")
    text = (
        "Đơn shop · 30 ngày gần nhất" if staff else "Đơn hàng của bạn"
    ) + f" · Trang {page.page + 1}"
    rows = [
        [
            InlineKeyboardButton(
                text=f"{item.title} · {str(item.id)[:8]}", callback_data=f"ord:{detail}:{item.id}"
            )
        ]
        for item in page.items
    ]
    if not rows:
        text += "\nChưa có đơn trong trang này."
    if page.page:
        rows.append(
            [InlineKeyboardButton(text="Trang trước", callback_data=f"ord:{mode}:{page.page - 1}")]
        )
    if page.has_more:
        rows.append(
            [InlineKeyboardButton(text="Trang sau", callback_data=f"ord:{mode}:{page.page + 1}")]
        )
    return text, InlineKeyboardMarkup(inline_keyboard=rows + navigation(staff)[1:])


def detail_view(view: OrderView, staff: bool, may_resend: bool):
    text = (
        f"Đơn: {view.id}\n{view.title}\nSKU: {view.sku}\n"
        f"Giá lúc mua: {view.price_stars} Stars\n"
        f"Trạng thái: {STATUS_LABELS.get(view.status, 'Chưa xác định')}\n"
        f"Tạo: {view.created_at:%d/%m/%Y %H:%M} UTC"
    )
    if staff:
        text += f"\nKhách mua (Telegram ID): {view.buyer_telegram_id}"
    if view.hold:
        text += "\nĐang tạm giữ để xử lý; chưa được gửi lại."
    if view.resend_state:
        text += "\nGửi lại: " + RESEND_LABELS[view.resend_state]
    rows = navigation(staff)
    if not staff:
        rows.insert(
            0, [InlineKeyboardButton(text="Hỗ trợ đơn này", callback_data=f"sp:order:{view.id}")]
        )
    if view.can_resend and may_resend:
        mode = "t" if staff else "r"
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    text="Xem lại / gửi lại tài nguyên", callback_data=f"ord:{mode}:{view.id}"
                )
            ],
        )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def confirmation(action: ResendAction):
    return (
        "Gửi lại cùng tài nguyên đã cấp về private chat của khách mua?\n"
        "Không cấp key mới. Có thể nhận thêm bản sao nội dung đã giao. "
        "Xác nhận trong 10 phút.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Xác nhận gửi lại", callback_data=f"ord:c:{action.id}"
                    ),
                    InlineKeyboardButton(text="Hủy", callback_data=f"ord:x:{action.id}"),
                ]
            ]
            + navigation(action.staff)
        ),
    )


def create_order_router(
    history: HistoryService, resends: ResendService, access: AccessService
) -> Router:
    router = Router(name="order_history")

    async def detail(actor_id, order_id, staff):
        may_resend = True
        if staff:
            actor = await access.authorize(actor_id, Permission.VIEW_ORDERS)
            may_resend = can(actor, Permission.MANAGE_FULFILLMENT)
        return detail_view(await history.detail(actor_id, order_id, staff=staff), staff, may_resend)

    @router.message(Command("orders", "staff_orders"))
    async def orders_command(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        actor_id, staff = message.from_user.id, command.command == "staff_orders"
        if staff:
            await access.authorize(actor_id, Permission.VIEW_ORDERS)
        if command.args:
            try:
                order_id = UUID(command.args.strip())
            except ValueError:
                raise InvalidInput("Invalid order identifier") from None
            text, keyboard = await detail(actor_id, order_id, staff)
        else:
            text, keyboard = page_view(await history.list(actor_id, page=0, staff=staff), staff)
        await message.answer(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    async def dashboard(actor_id):
        await access.authorize(actor_id, Permission.VIEW_ORDERS)
        report = await history.dashboard(actor_id)
        text = (
            f"Tổng quan đơn · từ {report.since:%d/%m/%Y %H:%M} UTC\n"
            f"Số đơn: {report.total}\nGiá trị đơn đã thanh toán: {report.paid_stars} Stars\n"
            "Theo giá lúc mua, chưa trừ hoàn tiền.\n"
        )
        text += "\n".join(
            f"{STATUS_LABELS.get(status, 'Chưa xác định')}: {count}"
            for status, count in report.status_counts
        )
        return text, InlineKeyboardMarkup(inline_keyboard=navigation(True))

    @router.message(Command("dashboard"))
    async def dashboard_command(message: Message) -> None:
        if message.from_user:
            text, keyboard = await dashboard(message.from_user.id)
            await message.answer(text, reply_markup=keyboard, parse_mode=None)

    @router.callback_query(F.data.startswith("ord:"))
    async def callback(query: CallbackQuery) -> None:
        if not isinstance(query.message, Message) or len((query.data or "").encode()) > 64:
            raise InvalidInput("Invalid order callback")
        actor_id = query.from_user.id
        try:
            _, operation, reference = (query.data or "").split(":")
            if operation in {"a", "s", "t", "d"}:
                await access.authorize(actor_id, Permission.VIEW_ORDERS)
            await query.answer()
            if operation in {"l", "a"}:
                page = int(reference)
                validate_page(page)
                text, keyboard = page_view(
                    await history.list(actor_id, page=page, staff=operation == "a"),
                    operation == "a",
                )
            elif operation in {"v", "s"}:
                text, keyboard = await detail(actor_id, UUID(reference), operation == "s")
            elif operation in {"r", "t"}:
                if operation == "t":
                    await access.authorize(actor_id, Permission.MANAGE_FULFILLMENT)
                text, keyboard = confirmation(
                    await resends.prepare(actor_id, UUID(reference), staff=operation == "t")
                )
            elif operation == "c":
                action = await resends.confirm(actor_id, UUID(reference))
                text, keyboard = (
                    RESEND_LABELS[action.status],
                    InlineKeyboardMarkup(inline_keyboard=navigation(action.staff)),
                )
            elif operation == "x":
                await resends.cancel(actor_id, UUID(reference))
                text, keyboard = (
                    "Đã hủy xác nhận gửi lại.",
                    InlineKeyboardMarkup(inline_keyboard=navigation()),
                )
            elif operation == "d" and reference == "0":
                text, keyboard = await dashboard(actor_id)
            else:
                raise ValueError
        except ValueError:
            raise InvalidInput("Invalid order callback") from None
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    return router
