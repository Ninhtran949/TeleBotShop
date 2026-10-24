import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from telebotshop.application.services.coupons import CouponService
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.coupons import CouponDraft, DiscountKind

HELP = """Coupon Credit — quyền tài chính PRICE
/coupon_create CODE PERCENT|FIXED VALUE EXPIRES_UTC CAP PER_USER SCOPE
EXPIRES_UTC: YYYY-MM-DDTHH:MM:SSZ
SCOPE: ALL hoặc PRODUCT:UUID hoặc CATEGORY:CODE (đúng danh mục, không gồm con).
/coupon_state CODE on|off
Cấu hình bất biến sau tạo; có thể bật/tắt. Refund không hoàn lượt coupon.
Khách nhập /coupon SKU CODE để xem giá và xác nhận trả Credit."""


def parse_coupon(args: str) -> CouponDraft:
    if len(args) > 500:
        raise InvalidInput("Invalid coupon form")
    parts = args.split()
    if len(parts) != 7 or any(re.fullmatch(r"[0-9]{1,15}", parts[i]) is None for i in (2, 4, 5)):
        raise InvalidInput("Invalid coupon form")
    code, kind, value, expires, cap, per_user, scope = parts
    product_id, category = None, None
    tag, _, target = scope.partition(":")
    try:
        if tag.upper() == "PRODUCT" and target:
            product_id = UUID(target)
        elif tag.upper() == "CATEGORY" and target:
            category = target
        elif scope.upper() != "ALL":
            raise ValueError
        return CouponDraft(
            code,
            DiscountKind(kind),
            int(value),
            datetime.fromisoformat(expires),
            int(cap),
            int(per_user),
            product_id,
            category,
        )
    except ValueError:
        raise InvalidInput("Invalid coupon form") from None


@dataclass(frozen=True)
class CreateCoupon:
    action_id: UUID
    draft: CouponDraft


@dataclass(frozen=True)
class ToggleCoupon:
    action_id: UUID
    code: str
    active: bool
    expected_active: bool


type CouponCommand = CreateCoupon | ToggleCoupon


def coupon_text(draft: CouponDraft) -> str:
    scope = str(draft.product_id) if draft.product_id else draft.category_code or "ALL"
    unit = "%" if draft.kind == DiscountKind.PERCENT else " Credit"
    return (
        f"{draft.code} · {draft.value}{unit}\nPhạm vi: {scope}\n"
        f"Hết hạn: {draft.expires_at.isoformat()}\n"
        f"Tổng lượt: {draft.usage_cap}; mỗi khách: {draft.per_user_limit}"
    )


def create_coupon_admin_router(
    coupons: CouponService,
    remember: Callable[[int, CouponCommand], str],
    resolve: Callable[[int, str], CouponCommand | None],
) -> Router:
    router = Router(name="coupon_admin")

    async def listing(actor, offset=0):
        rows = await coupons.list(actor, offset)
        buttons = []
        if offset:
            buttons.append(
                InlineKeyboardButton(text="← Trước", callback_data=f"cp:l:{max(0, offset - 20)}")
            )
        if len(rows) == 20 and offset < 10000:
            buttons.append(InlineKeyboardButton(text="Sau →", callback_data=f"cp:l:{offset + 20}"))
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=row.draft.code, callback_data="cp:d:" + row.draft.code)]
                for row in rows
            ]
            + ([buttons] if buttons else [])
            + [
                [InlineKeyboardButton(text="Quản trị", callback_data="adm:home")],
            ]
        )
        return (
            HELP
            + "\n\n"
            + "\n".join(
                f"{row.draft.code} · {'ON' if row.active else 'OFF'} · "
                f"{row.draft.value} {row.draft.kind}"
                for row in rows
            )
        ), keyboard

    @router.message(Command("coupons", "coupon_create", "coupon_state"))
    async def command(message: Message, command: CommandObject):
        if message.from_user is None:
            return
        actor = message.from_user.id
        if command.command == "coupons":
            text, keyboard = await listing(actor)
        else:
            await coupons.list(actor)
            action_id = uuid5(NAMESPACE_URL, f"coupon-admin:{actor}:{message.message_id}")
            if command.command == "coupon_create":
                draft = parse_coupon(command.args or "")
                change = CreateCoupon(action_id, draft)
                text = "Xác nhận tạo coupon Credit?\n" + coupon_text(draft)
            else:
                parts = (command.args or "").split()
                if len(parts) != 2 or parts[1] not in {"on", "off"}:
                    raise InvalidInput("Use /coupon_state CODE on|off")
                coupon = await coupons.get(actor, parts[0])
                change = ToggleCoupon(action_id, coupon.draft.code, parts[1] == "on", coupon.active)
                text = f"Xác nhận {'bật' if change.active else 'tắt'} coupon {change.code}?"
            token = remember(actor, change)
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Xác nhận", callback_data="cp:a:" + token)],
                    [InlineKeyboardButton(text="Quay lại", callback_data="cp:l:0")],
                ]
            )
        await message.answer(text[:3900], reply_markup=keyboard, parse_mode=None)

    @router.callback_query(F.data.startswith("cp:"))
    async def callback(query: CallbackQuery):
        if not isinstance(query.message, Message):
            return
        parts = (query.data or "").split(":")
        if len(parts) != 3:
            raise InvalidInput("Invalid coupon navigation")
        if parts[1] == "a":
            change = resolve(query.from_user.id, parts[2])
            if change is None:
                await query.answer("Phiên hết hạn. Mở lại /coupons.", show_alert=True)
                return
            if isinstance(change, CreateCoupon):
                await coupons.create(query.from_user.id, change.draft, change.action_id)
            else:
                await coupons.set_active(
                    query.from_user.id,
                    change.code,
                    change.active,
                    change.action_id,
                    expected_active=change.expected_active,
                )
            text, keyboard = await listing(query.from_user.id)
        elif parts[1] == "l" and re.fullmatch(r"[0-9]{1,5}", parts[2]):
            text, keyboard = await listing(query.from_user.id, int(parts[2]))
        elif parts[1] == "d":
            coupon = await coupons.get(query.from_user.id, parts[2])
            text = coupon_text(coupon.draft) + f"\n{'ON' if coupon.active else 'OFF'}"
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Danh sách coupon", callback_data="cp:l:0")],
                ]
            )
        else:
            raise InvalidInput("Invalid coupon navigation")
        await query.answer()
        try:
            await query.message.edit_text(text[:3900], reply_markup=keyboard, parse_mode=None)
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message.lower():
                raise

    return router
