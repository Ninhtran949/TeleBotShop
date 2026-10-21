from functools import partial
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import EditMessageText
from aiogram.types import File

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.payments import PaymentService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.bootstrap import create_dispatcher
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.identity import CustomerIdentity, Role
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.telegram import guard as guard_module
from tests.inventory import inventory_service, setup_inventory
from tests.telegram import TelegramRecorder, callback_update, document_update, message_update


@pytest.mark.asyncio
async def test_upload_confirm_stock_pages_retire_and_low_stock_use_real_db_outside_network(
    migrated_database,
    monkeypatch,
):
    await setup_inventory(migrated_database)
    factory = partial(unit_of_work, migrated_database)
    inventory = inventory_service(migrated_database, threshold=10)
    telegram = TelegramRecorder(monkeypatch)
    clock = {"now": 0}
    monkeypatch.setattr(guard_module, "monotonic", lambda: clock["now"])

    async def send(bot, method, timeout=None):
        assert migrated_database.pool.checkedout() == 0
        return await telegram.send(bot, method, timeout)

    async def get_file(reference):
        assert migrated_database.pool.checkedout() == 0
        return File(file_id="fake", file_unique_id="fake", file_path="documents/file_1.txt")

    async def download(path, destination, **kwargs):
        assert migrated_database.pool.checkedout() == 0
        destination.write(b"\n".join(f"FAKE-PRIVATE-{number}".encode() for number in range(9)))

    monkeypatch.setattr(telegram.bot.session, "make_request", AsyncMock(side_effect=send))
    get = AsyncMock(side_effect=get_file)
    monkeypatch.setattr(telegram.bot, "get_file", get)
    monkeypatch.setattr(telegram.bot, "download_file", AsyncMock(side_effect=download))
    dispatcher = create_dispatcher(
        IdentityService(factory),
        AccessService(factory),
        CatalogService(factory),
        inventory,
        PreCheckoutService(factory),
        PaymentService(factory),
    )

    async def feed(update):
        clock["now"] += 11
        await dispatcher.feed_update(telegram.bot, update)

    async def mutate(update, cancel=False):
        await feed(update)
        preview = telegram.calls[-1]
        reference = preview.reply_markup.inline_keyboard[0][1 if cancel else 0].callback_data
        await feed(callback_update(reference, actor=9001))
        return reference

    await feed(message_update("/admin", actor=9001))
    assert any(
        button.callback_data == "iv:home"
        for row in telegram.calls[-1].reply_markup.inline_keyboard
        for button in row
    )
    reference = await mutate(document_update("/import DEMO-1"))
    assert (await inventory.stock(9001, "DEMO-1")).summary.available == 9
    await feed(callback_update(reference, actor=9001))
    assert "trước đó" in next(
        call.text for call in reversed(telegram.calls) if isinstance(call, EditMessageText)
    )
    await feed(message_update("/stock DEMO-1", actor=9001))
    assert "Sẵn có: 9" in telegram.calls[-1].text
    next_page = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    assert next_page == "iv:stock:DEMO-1:1"
    await feed(callback_update(next_page, actor=9001))
    assert "Trang 2" in next(
        call.text for call in reversed(telegram.calls) if isinstance(call, EditMessageText)
    )
    unit = (await inventory.stock(9001, "DEMO-1")).units.items[0]
    await mutate(message_update(f"/retire DEMO-1 {unit.id}", actor=9001), cancel=True)
    assert (await inventory.stock(9001, "DEMO-1")).summary.available == 9
    await mutate(message_update(f"/retire DEMO-1 {unit.id}", actor=9001))
    assert (await inventory.stock(9001, "DEMO-1")).summary.retired == 1
    await feed(message_update("/lowstock", actor=9001))
    assert "DEMO-1: 8 sẵn có" in telegram.calls[-1].text
    await feed(document_update("/import DEMO-1", actor=101))
    assert get.await_count == 1
    assert "không có quyền" in telegram.calls[-1].text
    assert all("FAKE-PRIVATE" not in str(call.model_dump()) for call in telegram.calls)
    await telegram.bot.session.close()


@pytest.mark.asyncio
async def test_revocation_during_download_is_rechecked_before_persisting_import(
    migrated_database, monkeypatch
):
    await setup_inventory(migrated_database)
    factory = partial(unit_of_work, migrated_database)
    identity, access = IdentityService(factory), AccessService(factory)
    await identity.get_or_create_customer(CustomerIdentity(101, "fake_staff", "Fake staff"))
    grant = await access.prepare_permission(
        9001, 101, Role.ADMIN, Permission.MANAGE_INVENTORY, True
    )
    await access.confirm(9001, grant.id)
    inventory = inventory_service(migrated_database)
    telegram = TelegramRecorder(monkeypatch)
    monkeypatch.setattr(
        telegram.bot,
        "get_file",
        AsyncMock(
            return_value=File(
                file_id="fake", file_unique_id="fake", file_path="documents/file_1.txt"
            )
        ),
    )

    async def download(path, destination, **kwargs):
        assert migrated_database.pool.checkedout() == 0
        revoke = await access.prepare_permission(
            9001, 101, Role.ADMIN, Permission.MANAGE_INVENTORY, False
        )
        await access.confirm(9001, revoke.id)
        destination.write(b"FAKE-PRIVATE")

    monkeypatch.setattr(telegram.bot, "download_file", AsyncMock(side_effect=download))
    dispatcher = create_dispatcher(
        identity,
        access,
        CatalogService(factory),
        inventory,
        PreCheckoutService(factory),
        PaymentService(factory),
    )
    await dispatcher.feed_update(telegram.bot, document_update("/import DEMO-1", actor=101))
    assert "không có quyền" in telegram.calls[-1].text
    assert (await inventory.stock(9001, "DEMO-1")).summary.available == 0
    await telegram.bot.session.close()
