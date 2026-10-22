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
from telebotshop.application.services.refunds import RefundService
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.refunds import RefundState

STATE_TEXT = {
    RefundState.PENDING: "Chờ xác nhận; chưa gọi hoàn tiền.",
    RefundState.SENDING: "Đang xử lý; không gửi lệnh hoàn lại.",
    RefundState.SUCCEEDED: "Đã xác nhận hoàn tiền thành công.",
    RefundState.FAILED: "Lệnh hoàn chưa được chấp nhận; cần tạo yêu cầu mới nếu tiếp tục.",
    RefundState.UNCERTAIN: "Chưa rõ kết quả hoàn tiền; cần đối soát, không hoàn lại.",
    RefundState.CANCELLED: "Đã hủy yêu cầu chưa thực hiện.",
}


def payment_uuid(value):
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise InvalidInput("Invalid refund reference") from None


def view_text(view):
    text = f"Thanh toán: {view.payment_id}\n{view.amount} {view.currency}\n"
    text += (
        "Charge chính của đơn"
        if view.primary
        else "Charge cần kiểm tra riêng; không thu hồi entitlement của đơn chính"
    )
    if view.order_id:
        text += f"\nĐơn: {view.order_id}"
    text += "\n" + STATE_TEXT.get(view.state, "Chưa có yêu cầu hoàn tiền.")
    if not view.verified:
        text += f"\nChưa xác minh charge/payer. /verify_charge {view.payment_id} [OFFSET]"
    text += f"\nĐối soát bằng chứng hoàn: /reconcile_refund {view.payment_id} [OFFSET]"
    text += (
        f"\nTạo xác nhận: /refund {view.payment_id}\n"
        "Tham chiếu quyết định theo chính sách ở dòng tiếp theo."
    )
    if "UNCERTAIN" in (view.snapshot.work_state, view.snapshot.resend_state):
        text += (
            "\nNếu đã chấp thuận rủi ro giao hàng chưa rõ: "
            f"/refund_uncertain {view.payment_id}\n"
            "Tham chiếu bằng chứng/quyết định ở dòng tiếp theo."
        )
    return text


def preview(intent, view):
    text = f"Xác nhận hoàn toàn bộ {view.amount} Stars?\nThanh toán: {view.payment_id}"
    if view.order_id:
        text += f"\nĐơn: {view.order_id}"
    text += "\n" + (
        "Đơn chính sẽ chuyển đã hoàn tiền."
        if view.primary
        else "Chỉ hoàn charge này; giữ entitlement của charge chính."
    )
    text += (
        "\nChỉ xác nhận sau khi duyệt theo chính sách shop. "
        "Tài nguyên đã cấp không tự thu hồi hoặc nhập lại kho."
    )
    if intent.accept_uncertain:
        text += (
            "\nBạn chấp thuận hoàn khi kết quả giao/gửi lại chưa rõ; "
            "khách có thể đã nhận tài nguyên. Lịch sử uncertainty được giữ nguyên."
        )
    text += "\nXác nhận trong 10 phút; quyền hoặc trạng thái thay đổi cần tạo yêu cầu mới."
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Xác nhận hoàn tiền", callback_data=f"rf:confirm:{intent.id}"
                ),
                InlineKeyboardButton(text="Hủy", callback_data=f"rf:cancel:{intent.id}"),
            ],
            *back_keyboard().inline_keyboard,
        ]
    )
    return text, keyboard


def create_refund_admin_router(service: RefundService) -> Router:
    router = Router(name="refunds")
    options = dict(parse_mode=None, link_preview_options=LinkPreviewOptions(is_disabled=True))

    async def queue(actor_id, page):
        result = await service.queue(actor_id, page)
        text = f"Thanh toán / hoàn tiền · Trang {page + 1}\n" + (
            "Chưa có thanh toán."
            if not result.items
            else "Chọn thanh toán để xem trạng thái và quyết định theo chính sách."
        )
        rows = [
            [
                InlineKeyboardButton(
                    text=f"{item.amount} {item.currency} · {str(item.payment_id)[:8]}",
                    callback_data=f"rf:view:{item.payment_id}",
                )
            ]
            for item in result.items
        ]
        if page:
            rows.append(
                [InlineKeyboardButton(text="Trang trước", callback_data=f"rf:list:{page - 1}")]
            )
        if result.has_more:
            rows.append(
                [InlineKeyboardButton(text="Trang sau", callback_data=f"rf:list:{page + 1}")]
            )
        return text, InlineKeyboardMarkup(inline_keyboard=rows + back_keyboard().inline_keyboard)

    @router.message(Command("refunds"))
    async def browse(message: Message, command: CommandObject):
        if command.args:
            view = await service.detail(message.from_user.id, payment_uuid(command.args.strip()))
            await message.answer(view_text(view), reply_markup=back_keyboard(), **options)
        else:
            text, keyboard = await queue(message.from_user.id, 0)
            await message.answer(text, reply_markup=keyboard, **options)

    @router.message(Command("refund", "refund_uncertain"))
    async def prepare(message: Message, command: CommandObject):
        first, separator, reference = (command.args or "").partition("\n")
        if not separator:
            raise InvalidInput("Refund decision reference required")
        target = payment_uuid(first.strip())
        intent = await service.prepare(
            message.from_user.id,
            target,
            reference,
            accept_uncertain=command.command == "refund_uncertain",
        )
        view = await service.detail(message.from_user.id, target)
        text, keyboard = preview(intent, view)
        await message.answer(text, reply_markup=keyboard, **options)

    @router.message(Command("verify_charge", "reconcile_refund"))
    async def evidence(message: Message, command: CommandObject):
        args = (command.args or "").split()
        if not 1 <= len(args) <= 2:
            raise InvalidInput("Payment and optional transaction offset required")
        target = payment_uuid(args[0])
        try:
            offset = int(args[1]) if len(args) == 2 else 0
        except ValueError:
            raise InvalidInput("Invalid transaction offset") from None
        operation = service.verify if command.command == "verify_charge" else service.reconcile
        matched = await operation(message.from_user.id, target, offset)
        text = (
            "Đã lưu bằng chứng giao dịch khớp."
            if matched
            else "Trang giao dịch này chưa có bằng chứng khớp. Không suy ra chưa hoàn tiền; "
            "giữ nguyên trạng thái, không gửi lại lệnh hoàn."
        )
        view = await service.detail(message.from_user.id, target)
        await message.answer(
            text + "\n\n" + view_text(view), reply_markup=back_keyboard(), **options
        )

    @router.callback_query(F.data.startswith("rf:"))
    async def callback(query: CallbackQuery):
        parts = (query.data or "").split(":")
        if len(parts) != 3 or len(query.data) > 64:
            raise InvalidInput("Invalid refund callback")
        action, value = parts[1:]
        if action == "list":
            try:
                page = int(value)
            except ValueError:
                raise InvalidInput("Invalid refund page") from None
            text, keyboard = await queue(query.from_user.id, page)
        elif action == "view":
            view = await service.detail(query.from_user.id, payment_uuid(value))
            text, keyboard = view_text(view), back_keyboard()
        elif action == "confirm":
            target = payment_uuid(value)
            await query.answer("Đang kiểm tra yêu cầu hoàn tiền.")
            intent = await service.confirm(query.from_user.id, target)
            await query.message.edit_text(
                STATE_TEXT.get(intent.status, "Vui lòng kiểm tra lại /refunds."),
                reply_markup=back_keyboard(),
                **options,
            )
            return
        elif action == "cancel":
            await service.cancel(query.from_user.id, payment_uuid(value))
            text, keyboard = "Đã hủy yêu cầu chưa thực hiện.", back_keyboard()
        else:
            raise InvalidInput("Invalid refund action")
        await query.answer()
        await query.message.edit_text(text, reply_markup=keyboard, **options)

    return router
