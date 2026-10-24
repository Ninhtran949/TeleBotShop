from collections.abc import Awaitable, Callable

from aiogram.types import Update
from pydantic import ValidationError

from telebotshop.domain.errors import InvalidInput


async def dispatch_update(
    consume: Callable[[Update], Awaitable[object]], payload: dict[str, object]
) -> object:
    try:
        update = Update.model_validate(payload)
    except ValidationError:
        raise InvalidInput("Invalid Telegram update") from None
    return await consume(update)
