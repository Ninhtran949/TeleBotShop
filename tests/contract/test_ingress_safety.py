import logging
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.inventory import InventoryService
from telebotshop.bootstrap import create_dispatcher
from telebotshop.infrastructure.telegram.guard import IngressGuard
from telebotshop.infrastructure.telegram.logging import configure_telegram_logging
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
async def test_rate_limit_blocks_before_use_case_and_bounds_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = AsyncMock(spec=IdentityService)
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = create_dispatcher(
        identity,
        AsyncMock(spec=AccessService),
        AsyncMock(spec=CatalogService),
        AsyncMock(spec=InventoryService),
        AsyncMock(),
        AsyncMock(),
        resources=AsyncMock(),
        manual=AsyncMock(),
        history=AsyncMock(),
        resends=AsyncMock(),
        support=AsyncMock(),
    )
    for _ in range(10):
        await dispatcher.feed_update(telegram.bot, message_update("/start"))
    assert identity.get_or_create_customer.await_count == 8
    guard = IngressGuard()
    assert all(guard.admit(actor, 0) for actor in range(1, 4097))
    assert not guard.admit(4097, 0)
    assert len(guard.windows) == 4096
    assert guard.admit(4097, 10)
    assert len(guard.windows) == 1
    await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_database_errors_do_not_expose_secret_profile_or_report_success(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    identity = AsyncMock(spec=IdentityService)
    identity.get_or_create_customer.side_effect = SQLAlchemyError("fake_secret_profile_payload")
    telegram = TelegramRecorder(monkeypatch)
    dispatcher = create_dispatcher(
        identity,
        AsyncMock(spec=AccessService),
        AsyncMock(spec=CatalogService),
        AsyncMock(spec=InventoryService),
        AsyncMock(),
        AsyncMock(),
        resources=AsyncMock(),
        manual=AsyncMock(),
        history=AsyncMock(),
        resends=AsyncMock(),
        support=AsyncMock(),
    )
    await dispatcher.feed_update(telegram.bot, message_update("/start"))
    assert "fake_secret_profile_payload" not in caplog.text
    assert "telegram_database_failure" in caplog.text
    assert all(
        "Chào mừng" not in call.text and "fake_secret_profile_payload" not in call.text
        for call in telegram.calls
    )
    await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_unknown_callback_does_not_mutate_and_has_safe_navigation_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, access = AsyncMock(spec=IdentityService), AsyncMock(spec=AccessService)
    telegram = TelegramRecorder(monkeypatch)
    await create_dispatcher(
        identity,
        access,
        AsyncMock(spec=CatalogService),
        AsyncMock(spec=InventoryService),
        AsyncMock(),
        AsyncMock(),
        resources=AsyncMock(),
        manual=AsyncMock(),
        history=AsyncMock(),
        resends=AsyncMock(),
        support=AsyncMock(),
    ).feed_update(telegram.bot, callback_update("forged:owner:101"))
    assert not identity.get_my_profile.called and not access.confirm.called
    assert "/start" in telegram.calls[-1].text
    await telegram.bot.session.close()


def test_sdk_log_errors_cannot_include_exception_body_or_token_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_telegram_logging()
    try:
        raise RuntimeError("fake_secret_response_body")
    except RuntimeError:
        logging.getLogger("aiogram.event").error(
            "https://api.telegram.org/botFAKE_SECRET/method", exc_info=True
        )
    assert "telegram_sdk_error" in caplog.text
    assert "FAKE_SECRET" not in caplog.text
    assert "fake_secret_response_body" not in caplog.text
    assert caplog.records[-1].exc_info is None
