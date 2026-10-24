from uuid import NAMESPACE_URL, UUID, uuid5

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from telebotshop.application.services.topup_review import ManualPaymentReviewService
from telebotshop.application.services.wallet import WalletLedgerService
from telebotshop.application.services.wallet_checkout import WalletCheckoutService
from telebotshop.application.services.wallet_refunds import WalletRefundService
from telebotshop.domain.errors import InvalidInput


def review_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Pending / fraud / đối soát", callback_data="wp:list")],
            [InlineKeyboardButton(text="Kiểm tra ledger", callback_data="wp:reconcile")],
            [InlineKeyboardButton(text="Quản trị", callback_data="adm:home")],
        ]
    )


def create_wallet_admin_router(
    wallet: WalletLedgerService,
    checkout: WalletCheckoutService,
    reviews: ManualPaymentReviewService,
    refunds: WalletRefundService,
):
    router = Router(name="wallet_admin")

    async def queue(actor):
        rows = await reviews.queue(actor)
        text = "Top-ups cần kiểm tra\n" + "\n".join(
            f"{r.event_id} · {r.state} · {r.reason}" for r in rows
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"{r.state} · {str(r.event_id)[:8]}",
                        callback_data="wp:d:" + r.event_id.hex,
                    )
                ]
                for r in rows
            ]
            + review_keyboard().inline_keyboard
        )
        return text, keyboard

    @router.message(
        Command(
            "wallet_reviews",
            "wallet_ledger",
            "wallet_price",
            "wallet_approve",
            "credit_refund",
            "wallet_reject",
            "wallet_escalate",
        )
    )
    async def command(message: Message, command: CommandObject):
        if message.from_user is None:
            return
        actor = message.from_user.id
        lines = (command.args or "").splitlines()
        parts = lines[0].split() if lines else []
        action = uuid5(NAMESPACE_URL, f"wallet-admin:{actor}:{message.message_id}")
        keyboard = review_keyboard()
        try:
            if command.command == "wallet_reviews":
                text, keyboard = await queue(actor)
            elif command.command == "wallet_ledger" and len(parts) == 1:
                balance, entries = await wallet.operator_history(actor, int(parts[0]))
                text = f"Số dư: {balance.available:,} credit\n" + "\n".join(
                    f"{e.id} · {e.kind} · {e.delta:+,}" for e in entries
                )
            elif command.command == "wallet_price" and len(parts) == 2 and len(lines) >= 2:
                await checkout.set_price(actor, UUID(parts[0]), int(parts[1]), action, lines[1])
                text = "Đã lưu giá credit độc lập."
            elif (
                command.command
                in {"wallet_approve", "credit_refund", "wallet_reject", "wallet_escalate"}
                and len(lines) >= 3
            ):
                if command.command != "credit_refund" and len(parts) == 2:
                    decision = {
                        "wallet_approve": "APPROVE",
                        "wallet_reject": "REJECT",
                        "wallet_escalate": "ESCALATE",
                    }[command.command]
                    await reviews.prepare(
                        actor,
                        UUID(parts[0]),
                        None if parts[1] == "-" else UUID(parts[1]),
                        action,
                        lines[1],
                        lines[2],
                        decision=decision,
                    )
                    prefix = "wp:a:"
                    text = (
                        f"Xác nhận quyết định {decision}. "
                        "Kiểm tra buyer, số tiền và nguồn bằng chứng."
                    )
                elif command.command == "credit_refund" and len(parts) == 1:
                    await refunds.prepare(actor, UUID(parts[0]), action, lines[1], lines[2])
                    prefix = "wp:f:"
                    text = (
                        "Xác nhận hoàn credit vào ví; không hoàn tiền ngân hàng/crypto "
                        "hoặc restock key."
                    )
                else:
                    raise ValueError
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Xác nhận", callback_data=prefix + action.hex)],
                        [InlineKeyboardButton(text="Hủy", callback_data="adm:home")],
                    ]
                )
            else:
                raise ValueError
        except ValueError:
            raise InvalidInput(
                "Invalid wallet admin arguments; put reason/source on separate lines"
            ) from None
        await message.answer(text[:3900], reply_markup=keyboard, parse_mode=None)

    @router.callback_query(F.data.startswith("wp:"))
    async def callback(query: CallbackQuery):
        if not isinstance(query.message, Message):
            return
        actor = query.from_user.id
        parts = (query.data or "").split(":")
        keyboard = review_keyboard()
        if parts in [["wp", "home"], ["wp", "list"]]:
            text, keyboard = await queue(actor)
        elif parts == ["wp", "reconcile"]:
            result = await wallet.reconcile(actor)
            text = (
                f"Ledger cân bằng: {result.balanced}\nAccounts lệch: {result.mismatched_accounts}"
            )
        elif len(parts) == 3:
            try:
                reference = UUID(parts[2])
            except ValueError:
                raise InvalidInput("Invalid financial action") from None
            if parts[1] == "d":
                event, result, intent = await reviews.detail(actor, reference)
                text = (
                    f"Event: {reference}\nProvider: {event.provider}\n"
                    f"TXID: {event.transaction_id}\n"
                    f"Thực nhận: {event.amount} {event.asset}\nNhận tại: {event.recipient}\n"
                    f"Network: {event.network}\nConfirmations: {event.confirmations}\n"
                    f"State: {result.state}\nLý do: {result.reason}\n"
                    f"Top-up: {None if intent is None else intent.id}\n"
                    "/wallet_approve EVENT_UUID TOPUP_UUID, dòng tiếp lý do, "
                    "dòng cuối source reference. Không gửi credentials."
                )
            elif parts[1] == "a":
                result = await reviews.confirm(actor, reference)
                text = f"Kết quả đối soát: {result.state}\nTop-up: {result.intent_id}"
            elif parts[1] == "f":
                entry = await refunds.confirm(actor, reference)
                text = f"Đã hoàn {entry.delta:,} credit. Journal: {entry.id}"
            else:
                raise InvalidInput("Invalid wallet action")
        else:
            raise InvalidInput("Invalid wallet callback")
        await query.message.edit_text(text[:3900], reply_markup=keyboard, parse_mode=None)
        await query.answer()

    return router
