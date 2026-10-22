from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from telebotshop.admin.identity import back_keyboard
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.resources import ResourceService
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.catalog import (
    ManualMetadata,
    ProductType,
    ResourceFormat,
    ReusableMetadata,
    validate_code,
)
from telebotshop.domain.models.resources import (
    DigitalFilePayload,
    ManualPayload,
    ReusableDocumentPayload,
    ReusableTextPayload,
)

RESOURCE_HELP = (
    "Nguồn giao hàng file / tài nguyên dùng lại / xử lý thủ công\n"
    "File hoặc tài nguyên DOCUMENT: gửi document kèm caption /resource SKU.\n"
    "Tài nguyên TEXT: /resource SKU, xuống dòng rồi nhập nội dung (tối đa 4096 UTF-8 bytes).\n"
    "Thủ công: /resource SKU, xuống dòng rồi nhập hướng dẫn riêng cho staff "
    "(tối đa 2048 UTF-8 bytes). Thông báo chờ lấy từ thông tin sản phẩm.\n"
    "Nguồn mới cần xác nhận; không thay nội dung của đơn đã tạo. Không nhận URL tải file."
)


def create_resource_admin_router(resources: ResourceService, access: AccessService) -> Router:
    router = Router(name="admin_resources")

    @router.message(Command("resources"))
    async def help_command(message: Message) -> None:
        if message.from_user is None:
            return
        await access.authorize(message.from_user.id, Permission.MANAGE_INVENTORY)
        await message.answer(RESOURCE_HELP, reply_markup=back_keyboard(), parse_mode=None)

    @router.message(Command("resource"))
    async def register(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        actor_id = message.from_user.id
        await access.authorize(actor_id, Permission.MANAGE_INVENTORY)
        header, _, body = (command.args or "").partition("\n")
        sku = header.strip()
        validate_code(sku)
        product = await resources.product(actor_id, sku)
        kind, metadata = product.draft.product_type, product.draft.metadata
        if message.document is not None and not body:
            document = message.document
            if kind == ProductType.DIGITAL_FILE:
                payload = DigitalFilePayload(
                    document.file_id, document.file_name, document.mime_type
                )
            elif (
                isinstance(metadata, ReusableMetadata)
                and metadata.format == ResourceFormat.DOCUMENT
            ):
                payload = ReusableDocumentPayload(document.file_id)
            else:
                raise InvalidInput("Product does not accept a document")
        elif (
            message.document is None
            and isinstance(metadata, ReusableMetadata)
            and metadata.format == ResourceFormat.TEXT
        ):
            payload = ReusableTextPayload(body)
        elif message.document is None and isinstance(metadata, ManualMetadata):
            payload = ManualPayload(metadata.customer_notice, body)
        else:
            raise InvalidInput("Invalid resource submission")
        action = await resources.prepare(actor_id, sku, payload)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Xác nhận nguồn mới", callback_data=f"rs:confirm:{action.id}"
                    ),
                    InlineKeyboardButton(text="Hủy", callback_data=f"rs:cancel:{action.id}"),
                ]
            ]
        )
        await message.answer(
            f"Xác nhận nguồn giao hàng mới cho {sku} ({kind.value})?\n"
            "Chưa áp dụng. Hết hạn sau 10 phút; đơn cũ giữ nguồn đã chốt.",
            reply_markup=keyboard,
            parse_mode=None,
        )

    @router.callback_query(F.data.startswith("rs:"))
    async def callback(query: CallbackQuery) -> None:
        await access.authorize(query.from_user.id, Permission.MANAGE_INVENTORY)
        if not isinstance(query.message, Message) or len(query.data or "") > 64:
            raise InvalidInput("Invalid resource callback")
        if query.data == "rs:home":
            text = RESOURCE_HELP
        else:
            try:
                prefix, operation, reference = (query.data or "").split(":")
                action_id = UUID(reference)
            except ValueError:
                raise InvalidInput("Invalid resource callback") from None
            if prefix != "rs" or operation not in {"confirm", "cancel"}:
                raise InvalidInput("Invalid resource callback")
            if operation == "confirm":
                changed = await resources.confirm(query.from_user.id, action_id)
                text = (
                    "Đã áp dụng nguồn giao hàng." if changed else "Nguồn đã được áp dụng trước đó."
                )
            else:
                await resources.cancel(query.from_user.id, action_id)
                text = "Đã hủy nguồn đang chờ xác nhận."
        await query.answer()
        await query.message.edit_text(text, reply_markup=back_keyboard(), parse_mode=None)

    return router
