import re
from uuid import UUID, uuid5

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from telebotshop.application.services.checkout import CheckoutService
from telebotshop.bot.routers.orders import STATUS_LABELS
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.orders import InvoiceStatus, Order, OrderStatus


def checkout_view(order: Order, terms_version: str | None):
    text = (
        f"Đơn: {order.id}\n{order.title}\nGiá đã chốt: {order.price_stars} Stars\n"
        f"Trạng thái: {STATUS_LABELS[order.status]}\n"
        f"Giữ chỗ tới: {order.expires_at:%d/%m/%Y %H:%M} UTC"
    )
    rows = []
    if order.status == OrderStatus.AWAITING_PAYMENT:
        if order.invoice_status == InvoiceStatus.READY and terms_version is not None:
            text += (
                "\n\nĐọc Điều khoản trước khi đồng ý và mở hóa đơn Stars. "
                "Giá trên hóa đơn theo đơn này."
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Đồng ý điều khoản · Thanh toán",
                        callback_data=f"ck:p:{order.id.hex}:{terms_version}",
                    )
                ]
            )
            rows.append([InlineKeyboardButton(text="Điều khoản", callback_data="sp:terms:0")])
        elif order.invoice_status == InvoiceStatus.SENT:
            text += (
                "\n\nHóa đơn Stars đã gửi vào chat này. Mở hóa đơn để thanh toán; "
                "bot chỉ xác nhận khi nhận receipt hợp lệ."
            )
        elif order.invoice_status in {InvoiceStatus.SENDING, InvoiceStatus.UNCERTAIN}:
            text += (
                "\n\nKết quả gửi hóa đơn đang xử lý hoặc chưa rõ; "
                "không thanh toán lại hay tạo đơn khác. Dùng /paysupport để kiểm tra."
            )
        else:
            text += "\n\nChưa thể mở thanh toán. Vui lòng dùng /paysupport."
        rows.append(
            [
                InlineKeyboardButton(
                    text="Hủy đơn chưa thanh toán", callback_data=f"ck:c:{order.id.hex}"
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="Làm mới trạng thái", callback_data=f"ck:v:{order.id.hex}")],
            [InlineKeyboardButton(text="Hỗ trợ đơn này", callback_data=f"sp:order:{order.id}")],
            [
                InlineKeyboardButton(text="Đơn hàng", callback_data="ord:l:0"),
                InlineKeyboardButton(text="Trang chủ", callback_data="home"),
            ],
        ]
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def create_checkout_router(checkout: CheckoutService) -> Router:
    router = Router(name="customer_checkout")

    @router.message(Command("checkout"))
    async def reopen(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        try:
            order_id = UUID(command.args or "")
        except ValueError:
            raise InvalidInput("Invalid checkout reference") from None
        text, keyboard = checkout_view(
            await checkout.orders.get(message.from_user.id, order_id), checkout.terms_version
        )
        await message.answer(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.callback_query(F.data.startswith("buy:") | F.data.startswith("ck:"))
    async def action(query: CallbackQuery) -> None:
        if not isinstance(query.message, Message):
            return
        parts = (query.data or "").split(":")
        actor = query.from_user.id
        try:
            if len(parts) == 3 and parts[0] == "buy" and re.fullmatch("[0-9a-f]{16}", parts[2]):
                if checkout.terms_version is None:
                    await query.answer(
                        "Shop chưa hoàn thiện điều khoản để nhận đơn. Vui lòng dùng /paysupport.",
                        show_alert=True,
                    )
                    return
                product_id = UUID(parts[1])
                order = await checkout.prepare(
                    actor, product_id, uuid5(product_id, f"{actor}:{parts[2]}")
                )
            elif parts[:2] == ["ck", "p"] and len(parts) == 4:
                order_id = UUID(parts[2])
                await query.answer()
                order = await checkout.pay(actor, order_id, parts[3])
                text, keyboard = checkout_view(order, checkout.terms_version)
                await query.message.edit_text(
                    text,
                    reply_markup=keyboard,
                    parse_mode=None,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return
            elif len(parts) == 3 and parts[0] == "ck" and parts[1] in {"v", "c"}:
                order_id = UUID(parts[2])
                method = checkout.orders.get if parts[1] == "v" else checkout.orders.cancel
                order = await method(actor, order_id)
            else:
                raise InvalidInput("Invalid checkout action")
        except ValueError:
            raise InvalidInput("Invalid checkout reference") from None
        text, keyboard = checkout_view(order, checkout.terms_version)
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        await query.answer()

    return router
