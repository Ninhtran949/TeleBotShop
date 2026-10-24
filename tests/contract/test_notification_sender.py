from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from telebotshop.domain.models.notifications import (
    NotificationKind,
    NotificationRequest,
    NotificationState,
)
from telebotshop.infrastructure.telegram.notifications import TelegramNotificationSender
from tests.telegram import TelegramRecorder


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("success", NotificationState.SENT),
        ("unknown", NotificationState.UNCERTAIN),
        ("rate", NotificationState.FAILED),
    ],
)
async def test_notifications_send_only_safe_metadata_to_private_recipient_without_retry(
    monkeypatch, caplog, outcome, expected
):
    telegram = TelegramRecorder(monkeypatch)
    request = NotificationRequest(
        uuid4(),
        NotificationKind.TICKET_REPLY,
        uuid4(),
        101,
        datetime.now(UTC) + timedelta(seconds=60),
    )
    calls = []

    async def send(bot, method, timeout=None):
        calls.append(method)
        if outcome == "unknown":
            raise TelegramNetworkError(method=method, message="FAKE_SECRET_SDK_BODY")
        if outcome == "rate":
            raise TelegramRetryAfter(method=method, message="FAKE_SECRET_SDK_BODY", retry_after=10)
        return await telegram.send(bot, method, timeout)

    monkeypatch.setattr(telegram.bot.session, "make_request", AsyncMock(side_effect=send))
    try:
        result = await TelegramNotificationSender(telegram.bot).send(request)
        assert result.state == expected and len(calls) == 1
        method = calls[0]
        assert isinstance(method, SendMessage) and method.chat_id == 101
        assert method.parse_mode is None and method.link_preview_options.is_disabled
        assert str(request.target_id) in method.text and "/ticket " in method.text
        assert "FAKE_SECRET_SDK_BODY" not in caplog.text + repr(result)
    finally:
        await telegram.bot.session.close()
