from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, AnswerPreCheckoutQuery, TelegramMethod
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
        if isinstance(method, AnswerCallbackQuery | AnswerPreCheckoutQuery):
            return True
        return Message(
            message_id=20,
            date=datetime.now(UTC),
            chat=Chat(id=101, type="private"),
            from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
            text="Test reply",
        ).as_(bot)


def pre_checkout_update(
    *,
    payload: str = "x" * 32,
    query_id: str = "fake_query",
    actor: int = 101,
    currency: str = "XTR",
    amount: int = 100,
    is_bot: bool = False,
) -> Update:
    return Update.model_validate(
        {
            "update_id": 3,
            "pre_checkout_query": {
                "id": query_id,
                "from": {"id": actor, "is_bot": is_bot, "first_name": "Fake"},
                "currency": currency,
                "total_amount": amount,
                "invoice_payload": payload,
            },
        }
    )


def payment_update(
    *,
    payload: str = "x" * 32,
    charge: str = "FAKE-charge-1",
    actor: int | None = 101,
    amount: int = 100,
    **fields,
) -> Update:
    message = {
        "message_id": 4,
        "date": 1,
        "chat": {"id": 101, "type": "private"},
        "successful_payment": {
            "currency": "XTR",
            "total_amount": amount,
            "invoice_payload": payload,
            "telegram_payment_charge_id": charge,
            "provider_payment_charge_id": "",
            **fields,
        },
    }
    if actor is not None:
        message["from"] = {"id": actor, "is_bot": False, "first_name": "Fake"}
    return Update.model_validate({"update_id": 4, "message": message})


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


def document_update(
    caption: str,
    *,
    actor: int = 9001,
    file_size: int = 20,
    file_name: str = "fake.txt",
    mime_type: str = "text/plain",
) -> Update:
    payload = message_update(caption, actor=actor).model_dump(by_alias=True, exclude_none=True)
    message = payload["message"]
    message["caption"] = message.pop("text")
    message["caption_entities"] = message.pop("entities")
    message["document"] = {
        "file_id": "BQACAg_FAKE_DOCUMENT_ID_123456789",
        "file_unique_id": "fake_document",
        "file_size": file_size,
        "file_name": file_name,
        "mime_type": mime_type,
    }
    return Update.model_validate(payload)
