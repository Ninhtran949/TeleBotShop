from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from telebotshop.application.services.access import AccessService
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.access import ROLE_PERMISSIONS, Access, Permission
from telebotshop.domain.models.admin import AdminAction
from telebotshop.domain.models.identity import Role

PERMISSION_LABELS = {
    Permission.MANAGE_ACCESS: "Quản lý quyền",
    Permission.MANAGE_SETTINGS: "Cài đặt",
    Permission.MANAGE_CATALOG: "Quản lý sản phẩm/giá",
    Permission.MANAGE_INVENTORY: "Quản lý kho",
    Permission.VIEW_ORDERS: "Tra cứu đơn hàng",
    Permission.REFUND: "Hoàn tiền",
    Permission.MANAGE_TICKETS: "Xử lý hỗ trợ",
}


def admin_menu(actor: Access) -> tuple[str, InlineKeyboardMarkup]:
    text = "Quản trị\nQuyền hiện tại:\n" + "\n".join(
        f"• {PERMISSION_LABELS[permission]}" for permission in sorted(actor.permissions)
    )
    rows = []
    if Permission.MANAGE_ACCESS in actor.permissions:
        rows.append([InlineKeyboardButton(text="Quản lý quyền", callback_data="adm:access")])
    if Permission.MANAGE_SETTINGS in actor.permissions:
        rows.append([InlineKeyboardButton(text="Cài đặt", callback_data="adm:settings")])
    rows.append(
        [
            InlineKeyboardButton(text="Trang chủ", callback_data="home"),
            InlineKeyboardButton(text="Hủy", callback_data="cancel"),
        ]
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Quay lại", callback_data="adm:home"),
                InlineKeyboardButton(text="Trang chủ", callback_data="home"),
                InlineKeyboardButton(text="Hủy", callback_data="cancel"),
            ]
        ]
    )


def confirmation(action: AdminAction) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Xác nhận", callback_data=f"adm:confirm:{action.id}"),
                InlineKeyboardButton(text="Hủy", callback_data=f"adm:cancel:{action.id}"),
            ]
        ]
    )


def create_admin_router(access: AccessService) -> Router:
    router = Router(name="admin_identity")

    @router.message(Command("admin"))
    async def admin(message: Message) -> None:
        if message.from_user is None:
            return
        actor = await access.admin_access(message.from_user.id)
        text, keyboard = admin_menu(actor)
        await message.answer(text, reply_markup=keyboard, parse_mode=None)

    @router.message(Command("grant", "revoke"))
    async def change_permission(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        actor_id = message.from_user.id
        await access.authorize(actor_id, Permission.MANAGE_ACCESS)
        try:
            parts = (command.args or "").split()
            if len(parts) != 3 or len(command.args or "") > 128 or not parts[0].isascii():
                raise ValueError
            target_id, role, permission = int(parts[0]), Role(parts[1]), Permission(parts[2])
        except ValueError:
            raise InvalidInput("Invalid delegation") from None
        grant = command.command == "grant"
        action = await access.prepare_permission(actor_id, target_id, role, permission, grant)
        verb = "Cấp" if grant else "Thu hồi"
        await message.answer(
            f"{verb} quyền {PERMISSION_LABELS[permission]}\n"
            f"Telegram ID: {target_id}\nVai trò: {role}\n"
            "Xác nhận trong 10 phút. Nếu quyền đã thay đổi, hãy tạo yêu cầu mới.",
            reply_markup=confirmation(action),
            parse_mode=None,
        )

    @router.message(Command("setsupport"))
    async def set_support(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        actor_id = message.from_user.id
        await access.authorize(actor_id, Permission.MANAGE_SETTINGS)
        args = (command.args or "").split()
        if len(args) != 1 or len(args[0]) > 33:
            raise InvalidInput("Invalid support username")
        value = None if args[0] == "-" else args[0].removeprefix("@")
        action = await access.prepare_support(actor_id, value)
        await message.answer(
            "Xóa tên hỗ trợ?" if value is None else f"Đặt tên hỗ trợ: @{value}?",
            reply_markup=confirmation(action),
            parse_mode=None,
        )

    @router.callback_query(F.data.startswith("adm:"))
    async def navigation(query: CallbackQuery) -> None:
        if not isinstance(query.message, Message):
            return
        actor_id = query.from_user.id
        data = query.data or ""
        keyboard = back_keyboard()
        if data == "adm:home":
            actor = await access.admin_access(actor_id)
            text, keyboard = admin_menu(actor)
        elif data == "adm:settings":
            await access.authorize(actor_id, Permission.MANAGE_SETTINGS)
            settings = await access.settings(actor_id)
            current = (
                "Chưa cấu hình"
                if settings.support_username is None
                else "@" + settings.support_username
            )
            text = (
                f"Cài đặt\nTên hỗ trợ: {current}\n\n"
                "Đặt tên hỗ trợ: /setsupport <username>\nXóa tên hỗ trợ: /setsupport -"
            )
        elif data == "adm:access":
            await access.authorize(actor_id, Permission.MANAGE_ACCESS)
            admin_permissions = ", ".join(sorted(ROLE_PERMISSIONS[Role.ADMIN]))
            support_permissions = ", ".join(sorted(ROLE_PERMISSIONS[Role.SUPPORT]))
            text = (
                "Quản lý quyền\nNgười nhận cần /start trước.\n\n"
                "/grant <Telegram_ID> <ADMIN|SUPPORT> <quyền>\n"
                "/revoke <Telegram_ID> <ADMIN|SUPPORT> <quyền>\n\n"
                f"ADMIN: {admin_permissions}\n"
                f"SUPPORT: {support_permissions} (chỉ ticket được phân công)."
            )
        else:
            await access.authorize(actor_id, Permission.MANAGE_ACCESS)
            try:
                prefix, verb, token = data.split(":")
                if prefix != "adm" or verb not in {"confirm", "cancel"}:
                    raise ValueError
                action_id = UUID(token)
            except ValueError:
                raise InvalidInput("Invalid action") from None
            if verb == "confirm":
                applied = await access.confirm(actor_id, action_id)
                text = "Đã cập nhật." if applied else "Thao tác này đã được xử lý."
            else:
                await access.cancel(actor_id, action_id)
                text = "Đã đóng yêu cầu. Các thay đổi đã xác nhận trước đó vẫn có hiệu lực."
        await query.message.edit_text(text, reply_markup=keyboard, parse_mode=None)
        await query.answer()

    return router
