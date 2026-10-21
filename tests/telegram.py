from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, TelegramMethod
from aiogram.types import Chat, Message, Update, User

FAKE_TOKEN = "123456:FAKE_token_only_for_offline_contract_tests"


class TelegramRecorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.bot = Bot(FAKE_TOKEN)
        self.calls: list[TelegramMethod] = []
        monkeypatch.setattr(self.bot.session, "make_request", AsyncMock(side_effect=self.send))

    async def send(
        self, bot: Bot, method: TelegramMethod, timeout: int | None = None
    ) -> Message | bool:
        self.calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return Message(
            message_id=20,
            date=datetime.now(UTC),
            chat=Chat(id=101, type="private"),
            from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
            text="Test reply",
        ).as_(bot)


def message_update(
    text: str, *, actor: int = 101, chat_id: int | None = None, chat_type: str = "private"
) -> Update:
    return Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1,
                "text": text,
                "entities": [{"offset": 0, "length": len(text.split()[0]), "type": "bot_command"}],
                "from": {
                    "id": actor,
                    "is_bot": False,
                    "first_name": "Fake",
                    "username": "fake_user",
                },
                "chat": {"id": actor if chat_id is None else chat_id, "type": chat_type},
            },
        }
    )


def callback_update(
    data: str,
    *,
    actor: int = 101,
    chat_id: int | None = None,
    chat_type: str = "private",
    inaccessible: bool = False,
    inline: bool = False,
) -> Update:
    payload = {
        "id": "fake_query",
        "from": {"id": actor, "is_bot": False, "first_name": "Fake"},
        "chat_instance": "fake_instance",
        "data": data,
    }
    if inline:
        payload["inline_message_id"] = "fake_inline"
    else:
        payload["message"] = {
            "message_id": 20,
            "date": 0 if inaccessible else 1,
            "chat": {"id": actor if chat_id is None else chat_id, "type": chat_type},
            "from": {"id": 123456, "is_bot": True, "first_name": "Bot"},
        }
    return Update.model_validate({"update_id": 2, "callback_query": payload})


def photo_update(caption: str, *, actor: int = 9001) -> Update:
    payload = message_update(caption, actor=actor).model_dump(by_alias=True, exclude_none=True)
    message = payload["message"]
    message["caption"] = message.pop("text")
    message["caption_entities"] = message.pop("entities")
    message["photo"] = [
        {
            "file_id": "AgACAg_FAKE_PUBLIC_PHOTO_ID_123456789",
            "file_unique_id": "fake_public_photo",
            "width": 200,
            "height": 200,
        }
    ]
    return Update.model_validate(payload)
