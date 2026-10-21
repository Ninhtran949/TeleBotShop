import re
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
from telebotshop.application.services.catalog import CatalogService
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.catalog import (
    CategoryDraft,
    DigitalFileMetadata,
    LicenseMetadata,
    ManualMetadata,
    ProductDraft,
    ProductType,
    ResourceFormat,
    ReusableMetadata,
    SaleState,
    UniqueKeyMetadata,
)
from telebotshop.domain.models.catalog_action import CatalogAction

HELP = """Quản lý sản phẩm
Thêm danh mục: /category CODE | Tên
Danh mục con: /category CODE | Tên | PARENT_CODE

Thêm/sửa sản phẩm (mỗi phần một dòng):
/product SKU | CATEGORY_CODE | TYPE | STARS
Tên sản phẩm
Mô tả công khai
Thông tin theo loại bên dưới

UNIQUE_KEY: bỏ dòng cuối.
LICENSE: hướng dẫn kích hoạt công khai.
DIGITAL_FILE: tên_file | MIME (ví dụ demo.pdf | application/pdf).
REUSABLE_RESOURCE: TEXT hoặc DOCUMENT.
MANUAL_FULFILLMENT: thông báo xử lý thủ công cho khách.

Giá: /price SKU STARS
Hiện/ẩn: /sale SKU active hoặc /sale SKU hidden
Ảnh: gửi photo kèm caption /productimage SKU
Xóa ảnh: /productimage SKU clear

Chỉ nhập nội dung công khai, không nhập key/nội dung giao hàng. Sản phẩm mới mặc định ẩn.
SKU và TYPE giữ nguyên khi sửa. Mọi thay đổi cần bấm Xác nhận trong 10 phút."""


def parse_price(text: str) -> int:
    if not re.fullmatch(r"[0-9]{1,19}", text):
        raise InvalidInput("Invalid Stars price")
    return int(text)


def parse_product_form(args: str) -> ProductDraft:
    if len(args) > 2200:
        raise InvalidInput("Invalid product form")
    lines = args.replace("\r\n", "\n").split("\n")
    while len(lines) > 3 and lines[-1] == "":
        lines.pop()
    if len(lines) not in {3, 4}:
        raise InvalidInput("Invalid product form")
    header = [part.strip() for part in lines[0].split("|")]
    if len(header) != 4:
        raise InvalidInput("Invalid product form")
    sku, category, raw_kind, price = header
    try:
        kind = ProductType(raw_kind)
        extra = lines[3].strip() if len(lines) == 4 else ""
        match kind:
            case ProductType.UNIQUE_KEY:
                if extra:
                    raise ValueError
                metadata = UniqueKeyMetadata()
            case ProductType.LICENSE:
                metadata = LicenseMetadata(extra)
            case ProductType.DIGITAL_FILE:
                file_name, mime_type = [part.strip() for part in extra.split("|")]
                metadata = DigitalFileMetadata(file_name, mime_type)
            case ProductType.REUSABLE_RESOURCE:
                metadata = ReusableMetadata(ResourceFormat(extra))
            case ProductType.MANUAL_FULFILLMENT:
                metadata = ManualMetadata(extra)
        return ProductDraft(sku, category, kind, lines[1], lines[2], parse_price(price), metadata)
    except ValueError:
        raise InvalidInput("Invalid product form") from None


def confirmation(action: CatalogAction) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Xác nhận", callback_data=f"pc:confirm:{action.id}"),
                InlineKeyboardButton(text="Hủy", callback_data=f"pc:cancel:{action.id}"),
            ]
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Sản phẩm", callback_data="pc:list:0"),
                InlineKeyboardButton(text="Quản trị", callback_data="adm:home"),
                InlineKeyboardButton(text="Trang chủ", callback_data="home"),
            ]
        ]
    )


def create_catalog_admin_router(catalog: CatalogService, access: AccessService) -> Router:
    router = Router(name="catalog_admin")

    async def product_list(actor_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
        result = await catalog.admin_products(actor_id, page)
        rows = [
            [
                InlineKeyboardButton(
                    text=f"{product.draft.sku} · {product.draft.name}",
                    callback_data=f"pc:item:{product.draft.sku}",
                )
            ]
            for product in result.items
        ]
        paging = []
        if result.page:
            paging.append(
                InlineKeyboardButton(text="← Trước", callback_data=f"pc:list:{result.page - 1}")
            )
        if result.has_more and result.page < 9999:
            paging.append(
                InlineKeyboardButton(text="Sau →", callback_data=f"pc:list:{result.page + 1}")
            )
        if paging:
            rows.append(paging)
        rows.append([InlineKeyboardButton(text="Hướng dẫn thêm/sửa", callback_data="pc:help")])
        rows.extend(back_keyboard().inline_keyboard[0:1])
        text = f"Sản phẩm · Trang {result.page + 1}"
        if not result.items:
            text += "\nChưa có sản phẩm ở trang này."
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    @router.message(Command("products"))
    async def products(message: Message) -> None:
        if message.from_user is None:
            return
        await access.authorize(message.from_user.id, Permission.MANAGE_CATALOG)
        text, keyboard = await product_list(message.from_user.id, 0)
        await message.answer(text, reply_markup=keyboard, parse_mode=None)

    @router.message(Command("category", "product", "price", "sale", "productimage"))
    async def mutation(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        actor_id = message.from_user.id
        await access.authorize(actor_id, Permission.MANAGE_CATALOG)
        args = command.args or ""
        if len(args) > 2200:
            raise InvalidInput("Invalid catalog command")
        try:
            if command.command == "category":
                parts = [part.strip() for part in args.split("|")]
                if len(parts) not in {2, 3}:
                    raise ValueError
                draft = CategoryDraft(parts[0], parts[1], parts[2] if len(parts) == 3 else None)
                action = await catalog.prepare_category(actor_id, draft)
                text = f"Thêm danh mục {draft.code}: {draft.name}?"
            elif command.command == "product":
                draft = parse_product_form(args)
                action = await catalog.prepare_product(actor_id, draft)
                text = f"Lưu sản phẩm {draft.sku}: {draft.name}\nGiá: {draft.price_stars} Stars?"
            elif command.command == "price":
                sku, amount = args.split()
                price = parse_price(amount)
                action = await catalog.prepare_price(actor_id, sku, price)
                text = f"Đổi giá {sku} thành {price} Stars?"
            elif command.command == "sale":
                sku, state = args.split()
                sale = SaleState(state.upper())
                action = await catalog.prepare_sale(actor_id, sku, sale)
                text = f"{'Hiện' if sale == SaleState.ACTIVE else 'Ẩn'} sản phẩm {sku}?"
            else:
                parts = args.split()
                if message.photo and len(parts) == 1:
                    image_id = message.photo[-1].file_id
                elif not message.photo and len(parts) == 2 and parts[1] == "clear":
                    image_id = None
                else:
                    raise ValueError
                action = await catalog.prepare_image(actor_id, parts[0], image_id)
                text = f"{'Xóa' if image_id is None else 'Đổi'} ảnh công khai của {parts[0]}?"
        except ValueError:
            raise InvalidInput("Invalid catalog command") from None
        await message.answer(
            text,
            reply_markup=confirmation(action),
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.callback_query(F.data.startswith("pc:"))
    async def navigation(query: CallbackQuery) -> None:
        if not isinstance(query.message, Message):
            return
        actor_id = query.from_user.id
        await access.authorize(actor_id, Permission.MANAGE_CATALOG)
        data = query.data or ""
        parts = data.split(":")
        keyboard = back_keyboard()
        try:
            if len(parts) == 2 and parts[1] == "help":
                text = HELP
            elif len(parts) == 3 and parts[1] == "list":
                text, keyboard = await product_list(actor_id, int(parts[2]))
            elif len(parts) == 3 and parts[1] == "item":
                product = await catalog.admin_product(actor_id, parts[2])
                draft = product.draft
                text = (
                    f"{draft.name}\nSKU: {draft.sku}\nGiá: {draft.price_stars} Stars\n"
                    f"{'Đang hiển thị' if product.sale_state == SaleState.ACTIVE else 'Đang ẩn'}\n"
                    f"\nĐổi giá: /price {draft.sku} STARS\nThêm/sửa nội dung: xem Hướng dẫn."
                )
                state = (
                    SaleState.HIDDEN if product.sale_state == SaleState.ACTIVE else SaleState.ACTIVE
                )
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Ẩn" if state == SaleState.HIDDEN else "Hiện",
                                callback_data=f"pc:sale:{draft.sku}:{state}",
                            ),
                            InlineKeyboardButton(text="Hướng dẫn", callback_data="pc:help"),
                        ],
                        *back_keyboard().inline_keyboard,
                    ]
                )
            elif len(parts) == 4 and parts[1] == "sale":
                sale = SaleState(parts[3])
                action = await catalog.prepare_sale(actor_id, parts[2], sale)
                text = f"{'Hiện' if sale == SaleState.ACTIVE else 'Ẩn'} sản phẩm {parts[2]}?"
                keyboard = confirmation(action)
            elif len(parts) == 3 and parts[1] in {"confirm", "cancel"}:
                action_id = UUID(parts[2])
                if parts[1] == "confirm":
                    changed = await catalog.confirm(actor_id, action_id)
                    text = "Đã lưu thay đổi." if changed else "Thao tác này đã được xử lý."
                else:
                    await catalog.cancel(actor_id, action_id)
                    text = "Đã đóng yêu cầu. Các thay đổi đã xác nhận trước đó vẫn có hiệu lực."
            else:
                raise ValueError
        except ValueError:
            raise InvalidInput("Invalid catalog callback") from None
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        await query.answer()

    return router
