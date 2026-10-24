from uuid import NAMESPACE_URL, UUID, uuid5

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.support import SupportService
from telebotshop.bot.routers.orders import RESEND_LABELS, STATUS_LABELS
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import Page, validate_page
from telebotshop.domain.models.identity import CustomerIdentity
from telebotshop.domain.models.support import (
    TicketAction,
    TicketActionKind,
    TicketDetail,
    TicketKind,
    TicketState,
    TicketSummary,
)

KIND_LABELS = {
    TicketKind.GENERAL: "Hỗ trợ chung",
    TicketKind.PAYMENT: "Thanh toán Stars",
    TicketKind.DELIVERY: "Giao sản phẩm",
}
WARNING = "Không gửi mật khẩu, OTP, mã khôi phục hoặc thông tin thẻ thanh toán."


def back(staff=False):
    return [
        [
            InlineKeyboardButton(
                text="Danh sách ticket", callback_data="st:list:0" if staff else "sp:list:0"
            )
        ],
        [InlineKeyboardButton(text="Trung tâm hỗ trợ", callback_data="sp:home:0")],
        [
            InlineKeyboardButton(text="Trang chủ", callback_data="home"),
            InlineKeyboardButton(text="Hủy", callback_data="cancel"),
        ],
    ]


def ticket_list(page: Page[TicketSummary], staff: bool):
    prefix = "st" if staff else "sp"
    text = ("Ticket đang mở" if staff else "Yêu cầu hỗ trợ của bạn") + f" · Trang {page.page + 1}"
    rows = [
        [
            InlineKeyboardButton(
                text=f"{KIND_LABELS[item.kind]} · {str(item.id)[:8]}",
                callback_data=f"{prefix}:view:{item.id}:0",
            )
        ]
        for item in page.items
    ]
    if not rows:
        text += "\nChưa có ticket trong trang này."
    if page.page:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Trang trước", callback_data=f"{prefix}:list:{page.page - 1}"
                )
            ]
        )
    if page.has_more:
        rows.append(
            [InlineKeyboardButton(text="Trang sau", callback_data=f"{prefix}:list:{page.page + 1}")]
        )
    return text, InlineKeyboardMarkup(inline_keyboard=rows + back(staff)[1:])


def ticket_detail(detail: TicketDetail, staff: bool):
    ticket, page = detail.ticket, detail.messages
    prefix = "st" if staff else "sp"
    text = (
        f"Ticket: {ticket.id}\n{KIND_LABELS[ticket.kind]}\n"
        f"Trạng thái: {'Đang mở' if ticket.state == TicketState.OPEN else 'Đã đóng'}\n"
        f"Cập nhật: {ticket.updated_at:%d/%m/%Y %H:%M} UTC\n"
    )
    if detail.order:
        price = (
            str(detail.order.price_credits) + " credit"
            if detail.order.price_credits is not None
            else str(detail.order.price_stars) + " Stars"
        )
        text += f"Đơn: {detail.order.id}\n{detail.order.title} · {price}\n"
        text += "Trạng thái đơn: " + STATUS_LABELS.get(detail.order.status, "Chưa xác định") + "\n"
        if detail.order.resend_state:
            text += "Gửi lại: " + RESEND_LABELS[detail.order.resend_state] + "\n"
    if staff:
        text += "Phân công: " + ("Đã phân công" if ticket.assigned else "Chưa phân công") + "\n"
    text += f"\nTin nhắn · Trang {page.page + 1}\n"
    for post in page.items:
        text += (
            f"{'Hỗ trợ' if post.from_staff else 'Khách'} · "
            f"{post.created_at:%d/%m %H:%M} UTC\n{post.text}\n"
        )
    if not page.items:
        text += "Không có tin nhắn trong trang này.\n"
    rows = [[InlineKeyboardButton(text="Làm mới", callback_data=f"{prefix}:view:{ticket.id}:0")]]
    if page.page:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Tin mới hơn", callback_data=f"{prefix}:view:{ticket.id}:{page.page - 1}"
                )
            ]
        )
    if page.has_more:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Tin cũ hơn", callback_data=f"{prefix}:view:{ticket.id}:{page.page + 1}"
                )
            ]
        )
    if ticket.state == TicketState.OPEN:
        command = "staff_reply" if staff else "ticket_reply"
        text += f"\nPhản hồi: /{command} {ticket.id}\nNội dung trên dòng tiếp theo.\n{WARNING}"
        rows.append(
            [InlineKeyboardButton(text="Đóng ticket", callback_data=f"{prefix}:close:{ticket.id}")]
        )
    return text, InlineKeyboardMarkup(inline_keyboard=rows + back(staff))


def action_view(action: TicketAction, target_telegram_id: int | None = None):
    text = (
        "Xác nhận phân công lại ticket?"
        if action.kind == TicketActionKind.ASSIGN
        else "Xác nhận đóng ticket? Thao tác này không đổi thanh toán hoặc giao hàng."
    )
    text += f"\nTicket: {action.ticket_id}"
    if action.kind == TicketActionKind.ASSIGN:
        if action.target_id is not None and target_telegram_id is None:
            raise InvalidInput("Assignment preview unavailable")
        text += (
            f"\nNgười xử lý (Telegram ID): {target_telegram_id}"
            if action.target_id
            else "\nBỏ phân công nhân viên."
        )
    return (
        text + "\nXác nhận trong 10 phút; nếu ticket thay đổi, hãy tạo yêu cầu mới.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Xác nhận", callback_data=f"sp:confirm:{action.id}"),
                    InlineKeyboardButton(text="Hủy", callback_data=f"sp:cancel:{action.id}"),
                ]
            ]
            + back(action.staff)
        ),
    )


def create_support_router(service: SupportService, identity: IdentityService) -> Router:
    router = Router(name="support_tickets")

    async def home(payment=False):
        contact = await service.contact()
        text = "Hỗ trợ thanh toán Stars\n" if payment else "Trung tâm hỗ trợ TeleBotShop\n"
        text += (
            "Cửa hàng tiếp nhận yêu cầu về giao dịch và sản phẩm. "
            "Telegram không xử lý đơn mua thay cửa hàng.\n" + WARNING
        )
        if contact:
            text += f"\nLiên hệ cửa hàng: @{contact}"
        rows = [
            [InlineKeyboardButton(text=KIND_LABELS[kind], callback_data=f"sp:new:{kind}")]
            for kind in TicketKind
        ]
        rows.append([InlineKeyboardButton(text="Điều khoản", callback_data="sp:terms:0")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows + back())

    def terms(page):
        body, more = service.terms(page)
        if body is None:
            text = "Điều khoản chưa được cấu hình. Cửa hàng chưa sẵn sàng mở bán."
        else:
            text = f"Điều khoản cửa hàng · Trang {page + 1}\n\n{body}"
        rows = []
        if body is not None and page:
            rows.append(
                [InlineKeyboardButton(text="Trang trước", callback_data=f"sp:terms:{page - 1}")]
            )
        if more:
            rows.append(
                [InlineKeyboardButton(text="Trang sau", callback_data=f"sp:terms:{page + 1}")]
            )
        return text, InlineKeyboardMarkup(inline_keyboard=rows + back())

    async def order_help(actor_id, order_id):
        view = await service.order_context(actor_id, order_id)
        text = (
            f"Hỗ trợ đơn: {view.id}\n{view.title}\n"
            f"Trạng thái: {STATUS_LABELS.get(view.status, 'Chưa xác định')}\n"
            f"Tạo yêu cầu: /ticket PAYMENT {view.id}\nMô tả vấn đề trên dòng tiếp theo.\n{WARNING}"
        )
        return text, InlineKeyboardMarkup(inline_keyboard=back())

    @router.message(Command("support", "paysupport", "terms"))
    async def support_home(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        if command.command == "terms":
            text, keyboard = terms(0)
        elif command.args:
            try:
                reference = UUID(command.args.strip())
            except ValueError:
                raise InvalidInput(
                    "Use /support or /paysupport with an optional order ID"
                ) from None
            text, keyboard = await order_help(message.from_user.id, reference)
        else:
            text, keyboard = await home(command.command == "paysupport")
        await message.answer(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.message(Command("tickets", "staff_tickets"))
    async def list_command(message: Message, command: CommandObject) -> None:
        if message.from_user:
            staff = command.command == "staff_tickets"
            text, keyboard = ticket_list(
                await service.list(message.from_user.id, staff=staff), staff
            )
            await message.answer(text, reply_markup=keyboard, parse_mode=None)

    @router.message(Command("ticket"))
    async def ticket_command(message: Message, command: CommandObject) -> None:
        actor = message.from_user
        if actor is None:
            return
        header, separator, body = (command.args or "").partition("\n")
        try:
            if len(header) > 128:
                raise ValueError
            if not separator:
                text, keyboard = ticket_detail(
                    await service.detail(actor.id, UUID(header.strip())), False
                )
            else:
                parts = header.split()
                if len(parts) not in {1, 2}:
                    raise ValueError
                kind = TicketKind(parts[0])
                order_id = UUID(parts[1]) if len(parts) == 2 and parts[1] != "-" else None
                await identity.get_or_create_customer(
                    CustomerIdentity(
                        actor.id, actor.username, actor.full_name, actor.language_code or "vi"
                    )
                )
                request = uuid5(
                    NAMESPACE_URL, f"telebotshop:support:{actor.id}:{message.message_id}"
                )
                created = await service.create(actor.id, kind, body, request, order_id=order_id)
                text = (
                    f"Đã nhận ticket: {created.id}\n"
                    "Mở ticket để xem phản hồi hoặc bổ sung thông tin."
                )
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Mở ticket", callback_data=f"sp:view:{created.id}:0"
                            )
                        ]
                    ]
                    + back()
                )
        except ValueError:
            raise InvalidInput(
                "Use /ticket ID or /ticket GENERAL|PAYMENT|DELIVERY [ORDER_ID] "
                "followed by a message on a new line"
            ) from None
        await message.answer(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.message(Command("ticket_reply", "staff_reply"))
    async def reply_command(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        reference, separator, body = (command.args or "").partition("\n")
        try:
            if not separator or len(reference) > 40:
                raise ValueError
            ticket_id = UUID(reference.strip())
        except ValueError:
            raise InvalidInput("Use a ticket ID followed by a reply on a new line") from None
        staff = command.command == "staff_reply"
        request = uuid5(
            NAMESPACE_URL, f"telebotshop:support:{message.from_user.id}:{message.message_id}"
        )
        await service.reply(message.from_user.id, ticket_id, body, request, staff=staff)
        await message.answer(
            "Đã lưu phản hồi trong ticket. "
            "Khách xem phản hồi bằng cách mở ticket hoặc bấm Làm mới.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=back(staff)),
            parse_mode=None,
        )

    @router.message(Command("ticket_assign", "ticket_close", "staff_close"))
    async def action_command(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        try:
            parts = (command.args or "").split()
            if len(command.args or "") > 128 or not parts:
                raise ValueError
            ticket_id = UUID(parts[0])
            if command.command == "ticket_assign" and len(parts) == 2:
                target = None if parts[1] == "-" else int(parts[1])
                action = await service.prepare_assignment(message.from_user.id, ticket_id, target)
            elif command.command != "ticket_assign" and len(parts) == 1:
                action = await service.prepare_close(
                    message.from_user.id, ticket_id, staff=command.command == "staff_close"
                )
            else:
                raise ValueError
        except ValueError:
            raise InvalidInput("Invalid support action") from None
        text, keyboard = action_view(action, target if command.command == "ticket_assign" else None)
        await message.answer(text, reply_markup=keyboard, parse_mode=None)

    @router.callback_query(F.data.startswith("sp:") | F.data.startswith("st:"))
    async def callback(query: CallbackQuery) -> None:
        if not isinstance(query.message, Message) or len((query.data or "").encode()) > 64:
            raise InvalidInput("Invalid support callback")
        try:
            parts = (query.data or "").split(":")
            prefix, operation, reference = parts[:3]
            staff = prefix == "st"
            actor = query.from_user.id
            await query.answer()
            if operation == "view" and len(parts) == 4:
                page = int(parts[3])
                validate_page(page)
                text, keyboard = ticket_detail(
                    await service.detail(actor, UUID(reference), page=page, staff=staff), staff
                )
            elif len(parts) != 3:
                raise ValueError
            elif operation == "list":
                page = int(reference)
                validate_page(page)
                text, keyboard = ticket_list(
                    await service.list(actor, page=page, staff=staff), staff
                )
            elif not staff and operation == "home" and reference == "0":
                text, keyboard = await home()
            elif not staff and operation == "new":
                kind = TicketKind(reference)
                text = (
                    f"{KIND_LABELS[kind]}\nGửi /ticket {kind}\n"
                    "Mô tả trên dòng tiếp theo, tối đa 2.000 byte.\n"
                    "Nếu biết mã đơn, thêm mã đó sau loại yêu cầu.\n" + WARNING
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=back())
            elif not staff and operation == "terms":
                page = int(reference)
                validate_page(page)
                text, keyboard = terms(page)
            elif not staff and operation == "order":
                text, keyboard = await order_help(actor, UUID(reference))
            elif operation == "close":
                text, keyboard = action_view(
                    await service.prepare_close(actor, UUID(reference), staff=staff)
                )
            elif not staff and operation == "confirm":
                changed = await service.confirm(actor, UUID(reference))
                text, keyboard = (
                    ("Đã cập nhật ticket." if changed else "Yêu cầu đã được áp dụng trước đó."),
                    InlineKeyboardMarkup(inline_keyboard=back()),
                )
            elif not staff and operation == "cancel":
                await service.cancel(actor, UUID(reference))
                text, keyboard = "Đã hủy xác nhận.", InlineKeyboardMarkup(inline_keyboard=back())
            else:
                raise ValueError
        except (ValueError, IndexError):
            raise InvalidInput("Invalid support callback") from None
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    return router
