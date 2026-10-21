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
            allowed_updates=["message", "callback_query", "pre_checkout_query"],
            handle_as_tasks=True,
            tasks_concurrency_limit=32,
            close_bot_session=False,
        )
    assert not telegram.calls
    close.assert_awaited_once()
    assert migrated_database.pool.checkedout() == 0
    profile = await IdentityService(partial(unit_of_work, migrated_database)).get_my_profile(9001)
    assert profile.telegram_id == 9001


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ok", "error", "cancelled", "deadline"])
async def test_concurrent_polling_drains_or_cancels_updates_before_clients_close(
    migrated_database,
    monkeypatch,
    outcome,
):
    engine = migrated_database
    recorder = TelegramRecorder(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    events = []
    original_close = recorder.bot.session.close
    original_dispose = AsyncEngine.dispose

    async def close():
        events.append("bot_closed")
        await original_close()

    async def dispose(instance):
        if instance is engine:
            events.append("db_closed")
        await original_dispose(instance)

    async def polling(dispatcher, *args, **kwargs):
        async def update():
            try:
                async with unit_of_work(engine) as work:
                    await work.orders.now()
                    entered.set()
                    await release.wait()
            finally:
                events.append("update_finished")

        task = asyncio.create_task(update())
        dispatcher._handle_update_tasks.add(task)
        task.add_done_callback(dispatcher._handle_update_tasks.discard)
        await entered.wait()
        if outcome == "error":
            raise TelegramNetworkError(GetUpdates(), "FAKE-private-error")
        if outcome == "cancelled":
            raise asyncio.CancelledError

    monkeypatch.setattr(recorder.bot.session, "close", close)
    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(bootstrap, "Bot", lambda *args, **kwargs: recorder.bot)
    monkeypatch.setattr(
        recorder.bot,
        "get_webhook_info",
        AsyncMock(
            return_value=WebhookInfo(
                url="",
                has_custom_certificate=False,
                pending_update_count=0,
            )
        ),
    )
    monkeypatch.setattr(Dispatcher, "start_polling", polling)
    settings = BotSettings(
        app_env="test",
        database_url="postgresql+asyncpg://fake:fake@127.0.0.1/fake",
        bot_token=FAKE_TOKEN,
        owner_id=9001,
    )
    parent = asyncio.create_task(bootstrap.run_polling(settings, fake_inventory_settings()))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not parent.done() and "bot_closed" not in events and "db_closed" not in events
        if outcome != "deadline":
            release.set()
        if outcome == "error":
            with pytest.raises(TelegramNetworkError):
                await parent
        elif outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await parent
        else:
            await asyncio.wait_for(parent, 12)
        assert events == ["update_finished", "bot_closed", "db_closed"]
        assert engine.pool.checkedout() == 0
    finally:
        release.set()
        if not parent.done():
            parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)
