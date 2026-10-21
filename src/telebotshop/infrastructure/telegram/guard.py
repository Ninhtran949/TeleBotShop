import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from time import monotonic

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.exc import SQLAlchemyError

from telebotshop.domain.errors import AccessDenied, CatalogNotFound, InvalidAction, InvalidInput
from telebotshop.domain.models.inventory import InventoryCryptoError, InventoryImportError

logger = logging.getLogger(__name__)


class IngressGuard(BaseMiddleware):
    def __init__(self) -> None:
        # shortcut: one ingress, bounded local counters; use shared counters before multi-ingress.
        self.windows: OrderedDict[int, tuple[float, int]] = OrderedDict()

    def admit(self, actor_id: int, now: float) -> bool:
        while self.windows and next(iter(self.windows.values()))[0] + 10 <= now:
            self.windows.popitem(last=False)
        if actor_id not in self.windows:
            if len(self.windows) >= 4096:
                return False
            self.windows[actor_id] = (now, 1)
            return True
        started, count = self.windows[actor_id]
        if count >= 8:
            return False
        self.windows[actor_id] = (started, count + 1)
        return True

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, object]], Awaitable[object]],
        event: TelegramObject,
        data: dict[str, object],
    ) -> object:
        if not isinstance(event, Message | CallbackQuery):
            return None
        message = event.message if isinstance(event, CallbackQuery) else event
        actor = event.from_user
        try:
            if (
                not isinstance(message, Message)
                or message.date.timestamp() <= 0
                or actor is None
                or actor.is_bot
                or message.chat.type != "private"
                or message.chat.id != actor.id
            ):
                if isinstance(event, CallbackQuery):
                    await event.answer("Vui lòng mở chat riêng với bot.", show_alert=True)
                return None
            if not self.admit(actor.id, monotonic()):
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        "Bạn thao tác quá nhanh. Vui lòng đợi một chút.", show_alert=True
                    )
                return None
            try:
                return await handler(event, data)
            except AccessDenied:
                text = "Bạn không có quyền thực hiện thao tác này."
            except InventoryImportError as error:
                location = f" ở dòng {error.row}" if error.row is not None else ""
                text = (
                    f"Không thể nhập kho{location}. Mã lỗi: {error.code}. Kiểm tra file và thử lại."
                )
            except InventoryCryptoError:
                logger.warning("telegram_inventory_crypto_failure")
                text = "Không thể xác minh mã hóa kho. Vui lòng kiểm tra cấu hình bảo mật."
            except InvalidInput:
                text = "Dữ liệu không hợp lệ. Vui lòng kiểm tra hướng dẫn."
            except CatalogNotFound:
                text = "Sản phẩm hoặc danh mục không còn hiển thị. Vui lòng mở lại /shop."
            except InvalidAction:
                text = "Thao tác không còn hợp lệ. Vui lòng mở lại từ /admin."
            except SQLAlchemyError:
                logger.warning("telegram_database_failure")
                text = "Chưa thể xử lý yêu cầu. Vui lòng thử lại sau."
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            else:
                await event.answer(text, parse_mode=None)
        except (TelegramAPIError, TimeoutError, OSError):
            logger.warning("telegram_transport_failure")
        return None
