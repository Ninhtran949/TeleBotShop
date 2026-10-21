from dataclasses import replace
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

from telebotshop.application.services.catalog import CatalogService
from telebotshop.bot.keyboards.catalog import CatalogView, NavigationStore
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import (
    DigitalFileMetadata,
    LicenseMetadata,
    ManualMetadata,
    Product,
    ProductType,
    ResourceFormat,
    ReusableMetadata,
)

TYPE_LABELS = {
    ProductType.UNIQUE_KEY: "Mã kích hoạt riêng",
    ProductType.LICENSE: "License",
    ProductType.DIGITAL_FILE: "Tệp số",
    ProductType.REUSABLE_RESOURCE: "Tài nguyên số",
    ProductType.MANUAL_FULFILLMENT: "Xử lý thủ công",
}


def product_text(product: Product) -> str:
    draft = product.draft
    text = (
        f"{draft.name}\n{TYPE_LABELS[draft.product_type]}\n"
        f"Giá: {draft.price_stars} Stars\nSKU: {draft.sku}\n\n{draft.description}"
    )
    metadata = draft.metadata
    if isinstance(metadata, LicenseMetadata):
        text += "\n\nHướng dẫn: " + metadata.instructions
    elif isinstance(metadata, DigitalFileMetadata):
        text += f"\n\nTệp: {metadata.file_name} ({metadata.mime_type})"
    elif isinstance(metadata, ReusableMetadata):
        text += "\n\nĐịnh dạng: " + (
            "Văn bản" if metadata.format == ResourceFormat.TEXT else "Tệp tài liệu"
        )
    elif isinstance(metadata, ManualMetadata):
        text += "\n\n" + metadata.customer_notice
    # shortcut: availability/checkout arrive in T05/T06; no stock claims or order acceptance yet.
    return text + "\n\nChưa nhận đơn."


def create_catalog_router(catalog: CatalogService) -> Router:
    router = Router(name="customer_catalog")
    navigation = NavigationStore()

    async def render(actor_id: int, view: CatalogView) -> tuple[str, InlineKeyboardMarkup]:
        if view.kind == "categories":
            result = await catalog.categories(
                actor_id, parent_code=view.category_code, page=view.page
            )
            title = "Cửa hàng"
            if view.category_code is not None:
                category = await catalog.category(actor_id, view.category_code)
                title = category.draft.name
            token = navigation.remember(actor_id, view)
            rows = [
                [
                    InlineKeyboardButton(
                        text=category.draft.name,
                        callback_data=f"shop:c:{category.draft.code}:{token}",
                    )
                ]
                for category in result.items
            ]
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Tất cả sản phẩm"
                        if view.category_code is None
                        else "Sản phẩm trong danh mục",
                        callback_data=f"shop:a:{token}",
                    )
                ]
            )
            if not result.items:
                title += "\nChưa có danh mục ở trang này."
        else:
            result = await catalog.products(
                actor_id, category_code=view.category_code, query=view.query, page=view.page
            )
            title = "Sản phẩm" if view.query is None else f"Kết quả cho: {view.query}"
            token = navigation.remember(actor_id, view)
            rows = [
                [
                    InlineKeyboardButton(
                        text=f"{product.draft.name} · {product.draft.price_stars} Stars",
                        callback_data=f"shop:p:{product.id.hex}:{token}",
                    )
                ]
                for product in result.items
            ]
            if not result.items:
                title += "\nKhông tìm thấy sản phẩm đang hiển thị ở trang này."
        title += f"\nTrang {result.page + 1}"
        paging = []
        if result.page:
            paging.append(
                InlineKeyboardButton(text="← Trước", callback_data=f"shop:n:{token}:prev")
            )
        if result.has_more and result.page < 9999:
            paging.append(InlineKeyboardButton(text="Sau →", callback_data=f"shop:n:{token}:next"))
        if paging:
            rows.append(paging)
        rows.append([InlineKeyboardButton(text="Tìm kiếm", callback_data="shop:searchhelp")])
        if view.back is not None:
            rows.append(
                [InlineKeyboardButton(text="Quay lại", callback_data=f"shop:v:{view.back}")]
            )
        elif view.kind == "products":
            rows.append([InlineKeyboardButton(text="Danh mục", callback_data="shop:root")])
        rows.append(
            [
                InlineKeyboardButton(text="Trang chủ", callback_data="home"),
                InlineKeyboardButton(text="Hủy", callback_data="cancel"),
            ]
        )
        return title, InlineKeyboardMarkup(inline_keyboard=rows)

    @router.message(Command("shop", "search"))
    async def browse(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        if command.command == "search":
            view = CatalogView("products", query=(command.args or "").strip())
        else:
            view = CatalogView("categories")
        text, keyboard = await render(message.from_user.id, view)
        await message.answer(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.callback_query(F.data.startswith("shop:"))
    async def callback(query: CallbackQuery) -> None:
        if not isinstance(query.message, Message):
            return
        actor_id = query.from_user.id
        parts = (query.data or "").split(":")
        if parts == ["shop", "root"]:
            text, keyboard = await render(actor_id, CatalogView("categories"))
        elif parts == ["shop", "searchhelp"]:
            text = "Tìm sản phẩm: gửi /search kèm từ khóa (1–80 ký tự).\nVí dụ: /search bản quyền"
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Cửa hàng", callback_data="shop:root"),
                        InlineKeyboardButton(text="Trang chủ", callback_data="home"),
                        InlineKeyboardButton(text="Hủy", callback_data="cancel"),
                    ]
                ]
            )
        else:
            if (
                len(parts) == 3
                and parts[1] in {"v", "a"}
                or len(parts) == 4
                and parts[1] in {"n", "c", "p"}
            ):
                token = parts[2] if parts[1] in {"v", "a", "n"} else parts[3]
            else:
                raise InvalidInput("Invalid catalog callback")
            view = navigation.resolve(actor_id, token)
            if view is None:
                await query.answer(
                    "Phiên duyệt đã hết hạn. Vui lòng mở lại /shop.", show_alert=True
                )
                return
            if parts[1] == "v":
                text, keyboard = await render(actor_id, view)
            elif parts[1] == "a":
                text, keyboard = await render(
                    actor_id, CatalogView("products", view.category_code, back=token)
                )
            elif parts[1] == "n":
                if parts[3] not in {"prev", "next"}:
                    raise InvalidInput("Invalid catalog page")
                page = view.page + (1 if parts[3] == "next" else -1)
                text, keyboard = await render(actor_id, replace(view, page=page))
            elif parts[1] == "c":
                category = await catalog.category(actor_id, parts[2])
                kind = "categories" if category.draft.parent_code is None else "products"
                text, keyboard = await render(
                    actor_id, CatalogView(kind, category.draft.code, back=token)
                )
            else:
                try:
                    product_id = UUID(parts[2])
                except ValueError:
                    raise InvalidInput("Invalid product reference") from None
                product = await catalog.product(actor_id, product_id)
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="Quay lại", callback_data=f"shop:v:{token}"),
                            InlineKeyboardButton(text="Trang chủ", callback_data="home"),
                            InlineKeyboardButton(text="Hủy", callback_data="cancel"),
                        ]
                    ]
                )
                await query.message.edit_text(
                    product_text(product),
                    reply_markup=keyboard,
                    parse_mode=None,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                await query.answer()
                if product.image_file_id is not None:
                    await query.message.answer_photo(
                        product.image_file_id, caption=product.draft.name, parse_mode=None
                    )
                return
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        await query.answer()

    return router
