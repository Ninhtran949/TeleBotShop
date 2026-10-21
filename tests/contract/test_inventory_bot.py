from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Dispatcher
from aiogram.types import File

from telebotshop.admin.inventory import LimitedImportBuffer, create_inventory_admin_router
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.inventory import InventoryService
from telebotshop.domain.errors import AccessDenied
from telebotshop.domain.models.inventory import (
    MAX_IMPORT_BYTES,
    ImportFormat,
    InventoryAction,
    InventoryActionKind,
    InventoryCryptoError,
    InventoryImportError,
)
from telebotshop.infrastructure.telegram.guard import IngressGuard
from tests.telegram import TelegramRecorder, callback_update, document_update, message_update


def dispatcher(inventory, access):
    result = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    result.message.outer_middleware(guard)
    result.callback_query.outer_middleware(guard)
    result.include_router(create_inventory_admin_router(inventory, access))
    return result


@pytest.mark.asyncio
async def test_upload_is_memory_only_bounded_and_prepares_actor_bound_confirmation(monkeypatch):
    inventory, access = AsyncMock(spec=InventoryService), AsyncMock(spec=AccessService)
    action = InventoryAction(
        uuid4(),
        uuid4(),
        InventoryActionKind.IMPORT,
        uuid4(),
        None,
        2,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    inventory.prepare_import.return_value = action
    telegram = TelegramRecorder(monkeypatch)
    monkeypatch.setattr(
        telegram.bot,
        "get_file",
        AsyncMock(
            return_value=File(
                file_id="fake",
                file_unique_id="fake",
                file_size=20,
                file_path="documents/file_1.txt",
            )
        ),
    )

    async def download(path, destination, **kwargs):
        assert path == "documents/file_1.txt" and isinstance(destination, LimitedImportBuffer)
        destination.write(b"FAKE-private-A\nFAKE-private-B")

    monkeypatch.setattr(telegram.bot, "download_file", AsyncMock(side_effect=download))
    await dispatcher(inventory, access).feed_update(telegram.bot, document_update("/import DEMO-1"))
    inventory.prepare_import.assert_awaited_once_with(
        9001, "DEMO-1", b"FAKE-private-A\nFAKE-private-B", ImportFormat.TXT
    )
    inventory.confirm.assert_not_awaited()
    assert "FAKE-private" not in telegram.calls[-1].text
    buttons = telegram.calls[-1].reply_markup.inline_keyboard[0]
    assert buttons[0].callback_data == f"iv:confirm:{action.id}"
    assert all(len(button.callback_data.encode()) <= 64 for button in buttons)
    await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        document_update("/import DEMO-1"),
        message_update("/stock DEMO-1"),
        callback_update("iv:confirm:" + str(uuid4())),
    ],
)
async def test_missing_permission_denies_before_download_or_use_case(monkeypatch, update):
    inventory, access = AsyncMock(spec=InventoryService), AsyncMock(spec=AccessService)
    access.authorize.side_effect = AccessDenied("denied")
    telegram = TelegramRecorder(monkeypatch)
    download = AsyncMock()
    monkeypatch.setattr(telegram.bot, "get_file", download)
    await dispatcher(inventory, access).feed_update(telegram.bot, update)
    assert not inventory.mock_calls and not download.called
    assert "không có quyền" in telegram.calls[-1].text
    await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "name", "path", "actual_size"),
    [
        (MAX_IMPORT_BYTES + 1, "fake.txt", "documents/file_1.txt", 20),
        (20, "fake.exe", "documents/file_1.txt", 20),
        (20, "fake.txt", "https://private.invalid/file", 20),
        (20, "fake.txt", "../private/file", 20),
        (20, "fake.txt", "documents/file_1.txt", MAX_IMPORT_BYTES + 1),
    ],
)
async def test_untrusted_upload_metadata_paths_and_actual_size_are_bounded(
    monkeypatch, size, name, path, actual_size
):
    inventory, access = AsyncMock(spec=InventoryService), AsyncMock(spec=AccessService)
    telegram = TelegramRecorder(monkeypatch)
    monkeypatch.setattr(
        telegram.bot,
        "get_file",
        AsyncMock(
            return_value=File(file_id="fake", file_unique_id="fake", file_size=20, file_path=path)
        ),
    )

    async def download(path, destination, **kwargs):
        destination.write(b"x" * actual_size)

    monkeypatch.setattr(telegram.bot, "download_file", AsyncMock(side_effect=download))
    await dispatcher(inventory, access).feed_update(
        telegram.bot, document_update("/import DEMO-1", file_size=size, file_name=name)
    )
    inventory.prepare_import.assert_not_awaited()
    assert not any("Xác nhận nhập" in call.text for call in telegram.calls)
    await telegram.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [InventoryImportError(2, "duplicate"), InventoryCryptoError("FAKE-private-secret")]
)
async def test_import_errors_return_safe_row_code_and_never_echo_payload(
    monkeypatch, caplog, error
):
    inventory, access = AsyncMock(spec=InventoryService), AsyncMock(spec=AccessService)
    inventory.confirm.side_effect = error
    telegram = TelegramRecorder(monkeypatch)
    await dispatcher(inventory, access).feed_update(
        telegram.bot, callback_update("iv:confirm:" + str(uuid4()))
    )
    assert "FAKE-private-secret" not in caplog.text
    assert "FAKE-private-secret" not in telegram.calls[-1].text
    if isinstance(error, InventoryImportError):
        assert "2" in telegram.calls[-1].text and "duplicate" in telegram.calls[-1].text
    await telegram.bot.session.close()
