from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Dispatcher

from telebotshop.admin.resources import create_resource_admin_router
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.resources import ResourceService
from telebotshop.domain.errors import AccessDenied
from telebotshop.domain.models.catalog import DigitalFileMetadata, Product, ProductType, SaleState
from telebotshop.domain.models.resources import ResourceState, ResourceVersion
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.catalog import fake_product
from tests.telegram import TelegramRecorder, callback_update, document_update


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_registered_document_is_not_downloaded_or_echoed_and_only_confirmation_publishes(
    monkeypatch, denied
):
    resources, access = AsyncMock(spec=ResourceService), AsyncMock(spec=AccessService)
    draft = replace(
        fake_product(),
        product_type=ProductType.DIGITAL_FILE,
        metadata=DigitalFileMetadata("fake.txt", "text/plain"),
    )
    product = Product(uuid4(), draft, SaleState.ACTIVE, 1)
    resources.product.return_value = product
    action = ResourceVersion(
        uuid4(),
        product.id,
        draft.sku,
        draft.product_type,
        draft.metadata,
        uuid4(),
        1,
        None,
        datetime.now(UTC) + timedelta(minutes=10),
        ResourceState.PENDING,
    )
    resources.prepare.return_value = action
    if denied:
        access.authorize.side_effect = AccessDenied("denied")
    telegram = TelegramRecorder(monkeypatch)
    download, get_file = AsyncMock(), AsyncMock()
    monkeypatch.setattr(telegram.bot, "download_file", download)
    monkeypatch.setattr(telegram.bot, "get_file", get_file)
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_resource_admin_router(resources, access))
    try:
        await dispatcher.feed_update(telegram.bot, document_update("/resource DEMO-1"))
        download.assert_not_awaited()
        get_file.assert_not_awaited()
        resources.confirm.assert_not_awaited()
        if denied:
            resources.product.assert_not_awaited()
            resources.prepare.assert_not_awaited()
        else:
            resources.prepare.assert_awaited_once()
            sent = telegram.calls[-1]
            assert "file_id" not in sent.text
            data = sent.reply_markup.inline_keyboard[0][0].callback_data
            assert data == f"rs:confirm:{action.id}" and len(data.encode()) <= 64
            await dispatcher.feed_update(telegram.bot, callback_update(data, actor=9001))
            resources.confirm.assert_awaited_once_with(9001, action.id)
    finally:
        await telegram.bot.session.close()
