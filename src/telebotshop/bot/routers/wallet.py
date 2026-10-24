import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from telebotshop.application.services.coupons import CouponService
from telebotshop.application.services.topups import TopUpService
from telebotshop.application.services.wallet import WalletLedgerService
from telebotshop.application.services.wallet_checkout import WalletCheckoutService
from telebotshop.bot.keyboards.catalog import NavigationStore
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.coupons import CouponQuote
from telebotshop.domain.models.topups import TopUpIntent


@dataclass(frozen=True, repr=False)
class CreditChoice:
    product_id: UUID
    action_id: UUID
    price: int
    terms: str
    coupon: CouponQuote | None = None


def wallet_keyboard(*, bank_enabled=True, usdt_enabled=True, coupons_enabled=False):
    topup_buttons = []
    if bank_enabled:
        topup_buttons.append(InlineKeyboardButton(text="Nạp Bank / QR", callback_data="wal:bank"))
    if usdt_enabled:
        topup_buttons.append(
            InlineKeyboardButton(text="Nạp Binance / USDT", callback_data="wal:usdt")
        )
    return InlineKeyboardMarkup(
        inline_keyboard=([topup_buttons] if topup_buttons else [])
        + [
            *(
                [[InlineKeyboardButton(text="Mã giảm giá", callback_data="wal:coupon")]]
                if coupons_enabled
                else []
            ),
            [
                InlineKeyboardButton(text="Lịch sử ví", callback_data="wal:history"),
                InlineKeyboardButton(text="Lịch sử nạp", callback_data="wal:topups"),
            ],
            [
                InlineKeyboardButton(text="Cửa hàng", callback_data="shop:root"),
                InlineKeyboardButton(text="Hỗ trợ", callback_data="sp:home:0"),
            ],
            [InlineKeyboardButton(text="Trang chủ", callback_data="home")],
        ]
    )


def create_wallet_router(
    wallet: WalletLedgerService,
    checkout: WalletCheckoutService,
    topups: TopUpService,
    qr_link: Callable[[TopUpIntent], str] | None = None,
    *,
    coupons: CouponService | None = None,
    bank_enabled=True,
    usdt_enabled=True,
):
    router = Router(name="credit_wallet")
    choices = NavigationStore[CreditChoice]()

    def keyboard():
        return wallet_keyboard(
            bank_enabled=bank_enabled,
            usdt_enabled=usdt_enabled,
            coupons_enabled=coupons is not None,
        )

    def confirmation(actor, product_id, action_id, price, coupon=None):
        token = choices.remember(
            actor, CreditChoice(product_id, action_id, price, checkout.terms_version, coupon)
        )
        text = f"Xác nhận mua bằng Credit\nGiá: {price:,} credit\n"
        if coupon is not None:
            text = (
                f"Xác nhận mua bằng Credit\nMã: {coupon.code}\n"
                f"Giá gốc: {coupon.base_credits:,} credit\n"
                f"Giảm: {coupon.discount_credits:,} credit\nCần trả: {price:,} credit\n"
                "Refund chỉ hoàn Credit thực trả, không hoàn lượt coupon.\n"
            )
        text += "Bạn đồng ý điều khoản hiện hành khi xác nhận. Xem /terms trước khi mua."
        return text, InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Đồng ý — trả {price:,} credit", callback_data="wc:b:" + token
                    )
                ],
                *(
                    [[InlineKeyboardButton(text="Nhập mã giảm giá", callback_data="wal:coupon")]]
                    if coupons is not None
                    else []
                ),
                [InlineKeyboardButton(text="Hủy", callback_data="home")],
            ]
        )

    async def edit_view(query, text, markup):
        await query.answer()
        try:
            await query.message.edit_text(text[:3900], reply_markup=markup, parse_mode=None)
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message.lower():
                raise

    @router.message(Command("coupon"))
    async def apply_coupon(message: Message, command: CommandObject):
        parts = (command.args or "").split()
        if message.from_user is None:
            return
        if coupons is None or len(parts) != 2:
            raise InvalidInput("Use /coupon SKU CODE")
        actor = message.from_user.id
        quote = await coupons.preview(actor, parts[0], parts[1])
        action = uuid5(NAMESPACE_URL, f"coupon-checkout:{actor}:{message.message_id}")
        text, markup = confirmation(actor, quote.product_id, action, quote.total_credits, quote)
        await message.answer(text, reply_markup=markup, parse_mode=None)

    async def overview(actor):
        balance = await wallet.balance(actor)
        pending = sum(
            intent.credit_amount
            for intent in await topups.history(actor)
            if intent.state not in {"CREDITED", "REJECTED"}
        )
        return (
            f"Ví của tôi\nSố dư khả dụng: {balance.available:,} credit\n"
            f"Yêu cầu nạp đang chờ (trang gần nhất): {pending:,} credit\n"
            "Sandbox: credit thử nghiệm, không chuyển tiền thật."
        )

    @router.message(Command("wallet"))
    async def show_wallet(message: Message):
        if message.from_user:
            await message.answer(
                await overview(message.from_user.id),
                reply_markup=keyboard(),
                parse_mode=None,
            )

    @router.message(Command("topup"))
    async def create_topup(message: Message, command: CommandObject):
        if message.from_user is None:
            return
        parts = (command.args or "").split()
        if len(parts) != 2 or re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,18})?", parts[1]) is None:
            raise InvalidInput("Use /topup bank AMOUNT or /topup usdt AMOUNT")
        actor = message.from_user.id
        action = uuid5(NAMESPACE_URL, f"wallet-topup:{actor}:{message.message_id}")
        qr = None
        if parts[0] == "bank" and bank_enabled and parts[1].isdigit():
            intent = await topups.create_bank(actor, int(parts[1]), action)
            text = (
                f"Nạp Bank / QR — SANDBOX\nMã: {intent.id}\n"
                f"Tài khoản nhận: {intent.recipient}\nSố tiền: {intent.credit_amount:,} VND\n"
                f"Nội dung: {intent.reference}"
            )
            if qr_link:
                qr = qr_link(intent)
        elif parts[0] == "usdt" and usdt_enabled:
            intent = await topups.create_usdt(actor, Decimal(parts[1]), action)
            text = (
                f"Nạp USDT — SANDBOX\nMã: {intent.id}\nNetwork: {intent.network}\n"
                f"Token contract: {intent.token_contract}\nĐịa chỉ: {intent.recipient}\n"
                f"Số lượng: {intent.amount} USDT → {intent.credit_amount:,} credit\n"
                f"Tỷ giá snapshot: {intent.rate} VND/USDT ({intent.rate_source})\n"
                "Chỉ đúng mạng/token được chỉ định; sai mạng không tự cộng credit."
            )
        else:
            raise InvalidInput("Top-up method unavailable")
        text += (
            f"\nHiệu lực tới: {intent.expires_at:%H:%M:%S %d/%m/%Y} UTC\n"
            "Đang chờ đối soát nguồn nhận tiền."
        )
        if qr is not None:
            try:
                await message.answer_photo(
                    qr, caption=text, reply_markup=keyboard(), parse_mode=None
                )
                return
            except TelegramBadRequest:
                # A rejected QR image leaves the exact transfer instructions available.
                pass
        await message.answer(text, reply_markup=keyboard(), parse_mode=None)

    @router.callback_query(F.data.startswith("wal:"))
    async def wallet_navigation(query: CallbackQuery):
        if not isinstance(query.message, Message):
            return
        actor = query.from_user.id
        if query.data == "wal:home":
            text = await overview(actor)
        elif query.data == "wal:bank" and bank_enabled:
            text = (
                "Nạp Bank / QR: gửi /topup bank SỐ_VND\nVí dụ: /topup bank 100000\n"
                "Chỉ sandbox, không chuyển tiền thật."
            )
        elif query.data == "wal:usdt" and usdt_enabled:
            text = (
                "Nạp Binance / USDT: gửi /topup usdt SỐ_USDT\nVí dụ: /topup usdt 1.5\n"
                "Liên hệ hỗ trợ nếu bạn chưa có địa chỉ nhận thử nghiệm riêng."
            )
        elif query.data == "wal:history":
            entries = await wallet.history(actor)
            text = "Lịch sử ví (20 giao dịch gần nhất)\n" + "\n".join(
                f"{entry.created_at:%d/%m %H:%M} {entry.kind}: {entry.delta:+,} credit · {entry.id}"
                for entry in entries
            )
        elif query.data == "wal:topups":
            entries = await topups.history(actor)
            text = "Lịch sử nạp\n" + "\n".join(
                f"{entry.id} · {entry.rail} · {entry.credit_amount:,} credit · {entry.state}"
                for entry in entries
            )
        elif query.data == "wal:coupon" and coupons is not None:
            text = (
                "Mã giảm giá Credit\nGửi /coupon SKU MÃ\nVí dụ: /coupon DEMO-1 SAVE10\n"
                "SKU nằm trong chi tiết sản phẩm. Xem giá gốc, số giảm và số Credit cần trả "
                "trước khi xác nhận. Refund không hoàn lượt coupon."
            )
        else:
            raise InvalidInput("Invalid wallet navigation")
        await edit_view(query, text, keyboard())

    @router.callback_query(F.data.startswith("wc:"))
    async def purchase(query: CallbackQuery):
        if not isinstance(query.message, Message):
            return
        actor = query.from_user.id
        parts = (query.data or "").split(":")
        if len(parts) != 3:
            raise InvalidInput("Invalid credit checkout")
        if parts[1] == "p":
            try:
                product_id = UUID(parts[2])
            except ValueError:
                raise InvalidInput("Invalid product") from None
            price = await checkout.price(product_id)
            if price is None:
                raise InvalidInput("Credit price unavailable")
            text, reply_keyboard = confirmation(actor, product_id, uuid4(), price)
        elif parts[1] == "b":
            choice = choices.resolve(actor, parts[2])
            if choice is None:
                await query.answer("Phiên mua hết hạn. Mở lại sản phẩm.", show_alert=True)
                return
            order = await checkout.buy(
                actor,
                choice.product_id,
                choice.action_id,
                choice.price,
                choice.terms,
                coupon=choice.coupon,
            )
            text = (
                f"Đã thanh toán bằng Credit\nĐơn: {order.id}\nTrạng thái: {order.status}\n"
                "Worker đang xử lý giao hàng. Xem /orders để theo dõi."
            )
            reply_keyboard = keyboard()
        else:
            raise InvalidInput("Invalid credit checkout")
        await edit_view(query, text, reply_keyboard)

    return router
