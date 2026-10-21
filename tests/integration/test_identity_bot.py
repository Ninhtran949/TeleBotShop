from functools import partial
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.identity import IdentityService
from telebotshop.bootstrap import create_dispatcher
from telebotshop.domain.models.access import Permission
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
async def test_start_admin_confirm_revoke_and_settings_use_real_db_outside_telegram_calls(
    migrated_database: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = partial(unit_of_work, migrated_database)
    identity, access = IdentityService(factory), AccessService(factory)
    await identity.bootstrap_owner(9001)
    telegram = TelegramRecorder(monkeypatch)

    async def assert_closed_uow(
        bot: Bot,
        method: TelegramMethod,
        timeout: int | None = None,
    ) -> Message | bool:
        assert migrated_database.pool.checkedout() == 0
        return await telegram.send(bot, method, timeout)

    monkeypatch.setattr(
        telegram.bot.session, "make_request", AsyncMock(side_effect=assert_closed_uow)
    )
    dispatcher = create_dispatcher(identity, access, CatalogService(factory))
    await dispatcher.feed_update(telegram.bot, message_update("/start"))
    await dispatcher.feed_update(telegram.bot, message_update("/admin"))
    assert "không có quyền" in telegram.calls[-1].text
    await dispatcher.feed_update(
        telegram.bot, message_update("/grant 101 ADMIN catalog.manage", actor=9001)
    )
    preview = telegram.calls[-1]
    assert isinstance(preview, SendMessage)
    token = preview.reply_markup.inline_keyboard[0][0].callback_data
    await dispatcher.feed_update(telegram.bot, callback_update(token, actor=9001))
    assert (await access.authorize(101, Permission.MANAGE_CATALOG)).permissions == {
        Permission.MANAGE_CATALOG
    }
    await dispatcher.feed_update(telegram.bot, message_update("/admin"))
    assert "Quản trị" in telegram.calls[-1].text
    await dispatcher.feed_update(
        telegram.bot, message_update("/revoke 101 ADMIN catalog.manage", actor=9001)
    )
    token = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    await dispatcher.feed_update(telegram.bot, callback_update(token, actor=9001))
    await dispatcher.feed_update(telegram.bot, message_update("/admin"))
    assert "không có quyền" in telegram.calls[-1].text
    await dispatcher.feed_update(
        telegram.bot, message_update("/setsupport fake_support", actor=9001)
    )
    token = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    await dispatcher.feed_update(telegram.bot, callback_update(token, actor=9001))
    await dispatcher.feed_update(telegram.bot, callback_update("adm:settings", actor=9001))
    assert "fake_support" in telegram.calls[-2].text
    async with migrated_database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM audit_logs")) == 4
        assert await connection.scalar(text("SELECT count(*) FROM user_permissions")) == 0
    await telegram.bot.session.close()
