from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.models.identity import CustomerIdentity


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🛍️ Cửa hàng", callback_data="shop:root"),
                InlineKeyboardButton(text="🔎 Tìm kiếm", callback_data="shop:searchhelp"),
            ],
            [InlineKeyboardButton(text="👤 Tài khoản", callback_data="profile")],
            [InlineKeyboardButton(text="Đơn hàng đã mua", callback_data="ord:l:0")],
            [
                InlineKeyboardButton(text="Hỗ trợ", callback_data="sp:home:0"),
                InlineKeyboardButton(text="Điều khoản", callback_data="sp:terms:0"),
            ],
            [
                InlineKeyboardButton(text="Trang chủ", callback_data="home"),
                InlineKeyboardButton(text="Hủy", callback_data="cancel"),
            ],
        ]
    )


def create_customer_router(identity: IdentityService) -> Router:
    router = Router(name="customer_identity")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        actor = message.from_user
        if actor is None:
            return
        await identity.get_or_create_customer(
            CustomerIdentity(actor.id, actor.username, actor.full_name, actor.language_code or "vi")
        )
        await message.answer(
            "Chào mừng bạn! Chọn mục bên dưới.", reply_markup=home_keyboard(), parse_mode=None
        )

    @router.callback_query(F.data.in_({"home", "profile", "cancel"}))
    async def navigation(query: CallbackQuery) -> None:
        if not isinstance(query.message, Message):
            return
        text = "Chào mừng bạn! Chọn mục bên dưới."
        keyboard = home_keyboard()
        if query.data == "profile":
            profile = await identity.get_my_profile(query.from_user.id)
            text = (
                f"Tài khoản của bạn\nTelegram ID: {profile.telegram_id}\n"
                f"Đăng ký: {profile.registered_at:%d/%m/%Y} (UTC)"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Quay lại", callback_data="home"),
                        InlineKeyboardButton(text="Hủy", callback_data="cancel"),
                    ]
                ]
            )
        elif query.data == "cancel":
            text = "Đã hủy. Bạn có thể quay về trang chủ."
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Trang chủ", callback_data="home")]]
            )
        await query.message.edit_text(text, reply_markup=keyboard, parse_mode=None)
        await query.answer()

    return router
