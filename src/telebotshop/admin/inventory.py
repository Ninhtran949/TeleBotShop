import asyncio
import re
from io import BytesIO
from uuid import UUID

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from telebotshop.admin.identity import back_keyboard
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.inventory import InventoryService
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.catalog import Page, validate_code
from telebotshop.domain.models.inventory import (
    MAX_IMPORT_BYTES,
    ImportFormat,
    InventoryAction,
    InventoryImportError,
    InventoryState,
    StockPage,
    StockSummary,
)

INVENTORY_HELP = (
    "Quản lý kho UNIQUE_KEY / LICENSE\n"
    "Gửi document .txt/.csv kèm caption /import SKU.\n"
    "UTF-8; tối đa 1 MiB, 1.000 dòng. TXT: một key mỗi dòng; "
    "CSV: một cột key hoặc license_key đúng loại. Không gửi key trong caption.\n"
    "/stock SKU — số lượng và mã đơn vị\n"
    "/retire SKU UNIT_ID — ngừng bán đơn vị, cần xác nhận\n"
    "/lowstock — sản phẩm active có số lượng thấp\n"
    "Key không được hiển thị hoặc xuất ra từ màn hình quản trị."
)


class LimitedImportBuffer(BytesIO):
    def write(self, buffer: bytes) -> int:
        if self.tell() + len(buffer) > MAX_IMPORT_BYTES:
            raise InventoryImportError(None, "size")
        return super().write(buffer)


async def download_import(bot: Bot, document: Document) -> tuple[bytes, ImportFormat]:
    name = document.file_name
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 128
        or any(character in name for character in "/\\\r\n\x00")
        or document.mime_type
        not in {
            None,
            "text/plain",
            "text/csv",
            "application/csv",
            "application/octet-stream",
            "application/vnd.ms-excel",
        }
        or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", document.file_id)
        or bot.session.api.is_local
    ):
        raise InvalidInput("Invalid inventory document")
    try:
        format = ImportFormat(name.rpartition(".")[2].lower())
    except ValueError:
        raise InvalidInput("Only TXT/CSV inventory documents are supported") from None
    if document.file_size is not None and not 0 < document.file_size <= MAX_IMPORT_BYTES:
        raise InventoryImportError(None, "size")
    async with asyncio.timeout(12):
        file = await bot.get_file(document.file_id)
        path = file.file_path
        if (
            not isinstance(path, str)
            or len(path) > 512
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", path)
            or any(segment in {".", ".."} for segment in path.split("/"))
        ):
            raise InvalidInput("Invalid Telegram document path")
        if file.file_size is not None and not 0 < file.file_size <= MAX_IMPORT_BYTES:
            raise InventoryImportError(None, "size")
        with LimitedImportBuffer() as destination:
            await bot.download_file(path, destination=destination, timeout=10, chunk_size=65536)
            return destination.getvalue(), format


def confirmation(action: InventoryAction) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Xác nhận", callback_data=f"iv:confirm:{action.id}"),
                InlineKeyboardButton(text="Hủy", callback_data=f"iv:cancel:{action.id}"),
            ]
        ]
    )


def stock_view(stock: StockPage) -> tuple[str, InlineKeyboardMarkup]:
    summary, page = stock.summary, stock.units
    text = (
        f"Kho {summary.sku}\nSẵn có: {summary.available} · Đã retire: {summary.retired}"
        + ("\nCảnh báo: tồn kho thấp." if summary.low_stock else "")
        + f"\nTrang {page.page + 1}\n"
    )
    for unit in page.items:
        text += f"\n{unit.status} · {unit.id}\n"
        if unit.status == InventoryState.AVAILABLE:
            text += f"/retire {summary.sku} {unit.id}\n"
    if not page.items:
        text += "Chưa có đơn vị trong trang này."
    rows = []
    if page.page:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Trang trước", callback_data=f"iv:stock:{summary.sku}:{page.page - 1}"
                )
            ]
        )
    if page.has_more:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Trang sau", callback_data=f"iv:stock:{summary.sku}:{page.page + 1}"
                )
            ]
        )
    return text, InlineKeyboardMarkup(inline_keyboard=rows + back_keyboard().inline_keyboard)


def low_stock_view(page: Page[StockSummary]) -> tuple[str, InlineKeyboardMarkup]:
    text = f"Tồn kho thấp · Trang {page.page + 1}\n" + (
        "\n".join(f"• {item.sku}: {item.available} sẵn có" for item in page.items)
        if page.items
        else "Không có sản phẩm active dưới hoặc bằng ngưỡng cấu hình."
    )
    rows = [
        [InlineKeyboardButton(text=item.sku, callback_data=f"iv:stock:{item.sku}:0")]
        for item in page.items
    ]
    if page.page:
        rows.append(
            [InlineKeyboardButton(text="Trang trước", callback_data=f"iv:low:{page.page - 1}")]
        )
    if page.has_more:
        rows.append(
            [InlineKeyboardButton(text="Trang sau", callback_data=f"iv:low:{page.page + 1}")]
        )
    return text, InlineKeyboardMarkup(inline_keyboard=rows + back_keyboard().inline_keyboard)


def create_inventory_admin_router(inventory: InventoryService, access: AccessService) -> Router:
    router = Router(name="admin_inventory")

    @router.message(Command("inventory"))
    async def help_command(message: Message) -> None:
        if message.from_user is None:
            return
        await access.authorize(message.from_user.id, Permission.MANAGE_INVENTORY)
        await message.answer(INVENTORY_HELP, reply_markup=back_keyboard(), parse_mode=None)

    @router.message(Command("import"))
    async def import_command(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        await access.authorize(message.from_user.id, Permission.MANAGE_INVENTORY)
        sku = (command.args or "").strip()
        validate_code(sku)
        if message.document is None:
            raise InvalidInput("Upload a TXT/CSV document with /import SKU as its caption")
        content, format = await download_import(message.bot, message.document)
        action = await inventory.prepare_import(message.from_user.id, sku, content, format)
        await message.answer(
            f"Xác nhận nhập {action.quantity} đơn vị vào {sku}?\n"
            "Chưa ghi vào tồn kho. Hết hạn sau 10 phút.",
            reply_markup=confirmation(action),
            parse_mode=None,
        )

    @router.message(Command("retire"))
    async def retire_command(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        await access.authorize(message.from_user.id, Permission.MANAGE_INVENTORY)
        try:
            sku, reference = (command.args or "").split()
            unit_id = UUID(reference)
        except ValueError:
            raise InvalidInput("Use /retire SKU UNIT_ID") from None
        action = await inventory.prepare_retire(message.from_user.id, sku, unit_id)
        await message.answer(
            f"Xác nhận retire đơn vị {unit_id} của {sku}?\n"
            "Đơn vị sẽ ngừng bán; payload vẫn được giữ. Hết hạn sau 10 phút.",
            reply_markup=confirmation(action),
            parse_mode=None,
        )

    @router.message(Command("stock", "lowstock"))
    async def stock_command(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        await access.authorize(message.from_user.id, Permission.MANAGE_INVENTORY)
        if command.command == "stock":
            text, keyboard = stock_view(
                await inventory.stock(message.from_user.id, (command.args or "").strip())
            )
        else:
            if command.args:
                raise InvalidInput("Use /lowstock")
            text, keyboard = low_stock_view(await inventory.low_stock(message.from_user.id))
        await message.answer(text, reply_markup=keyboard, parse_mode=None)

    @router.callback_query(F.data.startswith("iv:"))
    async def callback(query: CallbackQuery) -> None:
        await access.authorize(query.from_user.id, Permission.MANAGE_INVENTORY)
        if not isinstance(query.message, Message):
            raise InvalidInput("Open the private bot chat")
        parts = (query.data or "").split(":")
        if len(query.data or "") > 64:
            raise InvalidInput("Invalid inventory callback")
        try:
            match parts:
                case ["iv", "home"]:
                    text, keyboard = INVENTORY_HELP, back_keyboard()
                case ["iv", "confirm", reference]:
                    changed = await inventory.confirm(query.from_user.id, UUID(reference))
                    text = "Đã cập nhật kho." if changed else "Thao tác đã được áp dụng trước đó."
                    keyboard = back_keyboard()
                case ["iv", "cancel", reference]:
                    await inventory.cancel(query.from_user.id, UUID(reference))
                    text, keyboard = (
                        "Đã hủy thao tác đang chờ; không thay đổi kho.",
                        back_keyboard(),
                    )
                case ["iv", "stock", sku, page]:
                    text, keyboard = stock_view(
                        await inventory.stock(query.from_user.id, sku, int(page))
                    )
                case ["iv", "low", page]:
                    text, keyboard = low_stock_view(
                        await inventory.low_stock(query.from_user.id, int(page))
                    )
                case _:
                    raise InvalidInput("Invalid inventory callback")
        except InvalidInput:
            raise
        except ValueError:
            raise InvalidInput("Invalid inventory callback") from None
        await query.message.edit_text(text, reply_markup=keyboard, parse_mode=None)
        await query.answer()

    return router
