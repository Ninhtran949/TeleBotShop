import asyncio
import base64
from functools import partial
from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetUpdates
from aiogram.types import WebhookInfo
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop import bootstrap
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.infrastructure.config import BotSettings
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.inventory import fake_inventory_settings, inventory_service, setup_inventory
from tests.telegram import FAKE_TOKEN, TelegramRecorder


@pytest.mark.asyncio
async def test_polling_checks_registered_keys_before_any_telegram_connection(
    migrated_database, monkeypatch
):
    await setup_inventory(migrated_database)
    await inventory_service(migrated_database).check_keys()
    settings = fake_inventory_settings()
    changed = type(settings)(
        **{
            **settings.model_dump(),
            "inventory_dedup_key": base64.b64encode(b"e" * 32).decode(),
        }
    )
    bot = AsyncMock(side_effect=AssertionError("Telegram must not be initialized"))
    monkeypatch.setattr(bootstrap, "Bot", bot)
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: migrated_database)
    bot_settings = BotSettings(
        app_env="test",
        database_url="postgresql+asyncpg://fake:fake@127.0.0.1/fake",
        bot_token=FAKE_TOKEN,
        owner_id=9001,
    )
    with pytest.raises(InventoryCryptoError):
        await bootstrap.run_polling(bot_settings, changed)
    assert not bot.called and migrated_database.pool.checkedout() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("webhook", "outcome"),
    [
        ("", "ok"),
        ("https://fake-webhook.invalid/private", "ok"),
        ("", "error"),
        ("", "cancelled"),
    ],
)
async def test_polling_preserves_webhook_closes_resources_and_bootstraps_owner(
    migrated_database: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    webhook: str,
    outcome: str,
) -> None:
    telegram = TelegramRecorder(monkeypatch)
    close = AsyncMock(wraps=telegram.bot.session.close)
    monkeypatch.setattr(telegram.bot.session, "close", close)
    monkeypatch.setattr(bootstrap, "Bot", lambda *args, **kwargs: telegram.bot)
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: migrated_database)
    info = AsyncMock(
        return_value=WebhookInfo(url=webhook, has_custom_certificate=False, pending_update_count=0)
    )
    monkeypatch.setattr(telegram.bot, "get_webhook_info", info)
    polling = AsyncMock()
    if outcome == "error":
        polling.side_effect = TelegramNetworkError(
            method=GetUpdates(), message="fake_secret_payload"
        )
    elif outcome == "cancelled":
        polling.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(Dispatcher, "start_polling", polling)
    settings = BotSettings(
        app_env="test",
        database_url="postgresql+asyncpg://fake:fake@127.0.0.1/fake",
        bot_token=FAKE_TOKEN,
        owner_id=9001,
    )
    if webhook:
        with pytest.raises(bootstrap.PollingStartupError, match="Webhook is configured"):
            await bootstrap.run_polling(settings, fake_inventory_settings())
        polling.assert_not_awaited()
    else:
        if outcome == "error":
            with pytest.raises(TelegramNetworkError):
                await bootstrap.run_polling(settings, fake_inventory_settings())
        elif outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await bootstrap.run_polling(settings, fake_inventory_settings())
        else:
            await bootstrap.run_polling(settings, fake_inventory_settings())
        polling.assert_awaited_once_with(
            telegram.bot,
            allowed_updates=["message", "callback_query"],
            handle_as_tasks=False,
            close_bot_session=False,
        )
    assert not telegram.calls
    close.assert_awaited_once()
    assert migrated_database.pool.checkedout() == 0
    profile = await IdentityService(partial(unit_of_work, migrated_database)).get_my_profile(9001)
    assert profile.telegram_id == 9001
