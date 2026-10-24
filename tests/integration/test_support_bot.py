from functools import partial
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import text

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.history import HistoryService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.payments import PaymentService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.application.services.support import SupportService
from telebotshop.bootstrap import create_dispatcher
from telebotshop.domain.models.support import PublicTerms
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.checkout import checkout_service
from tests.fulfillment import paid_work
from tests.integration.test_support import staff
from tests.inventory import fake_cipher, inventory_service
from tests.manual import manual_service
from tests.refunds import refund_service
from tests.resends import RecordingSender, resend_service
from tests.resources import resource_service
from tests.telegram import TelegramRecorder, callback_update, message_update


@pytest.mark.asyncio
async def test_native_private_support_assignment_reply_and_order_help(
    migrated_database, monkeypatch, caplog
):
    engine = migrated_database
    order, _ = await paid_work(engine)
    await staff(engine)
    await staff(engine, 201)
    factory = partial(unit_of_work, engine)
    support = SupportService(
        factory, fake_cipher(), PublicTerms("FAKE TEST TERMS <&>\nOnly test content.")
    )
    telegram = TelegramRecorder(monkeypatch)
    clock = {"now": 0}
    monkeypatch.setattr("telebotshop.infrastructure.telegram.guard.monotonic", lambda: clock["now"])

    async def send(bot, method, timeout=None):
        assert engine.pool.checkedout() == 0
        return await telegram.send(bot, method, timeout)

    monkeypatch.setattr(telegram.bot.session, "make_request", AsyncMock(side_effect=send))
    dispatcher = create_dispatcher(
        IdentityService(factory),
        AccessService(factory),
        CatalogService(factory),
        inventory_service(engine),
        PreCheckoutService(factory),
        PaymentService(factory),
        resources=resource_service(engine),
        manual=manual_service(engine),
        history=HistoryService(factory),
        resends=resend_service(engine, RecordingSender(engine)),
        support=support,
        refunds=refund_service(engine),
        checkout=checkout_service(engine),
    )
    sequence = 20

    async def feed(update, *, replay=False):
        nonlocal sequence
        clock["now"] += 2
        if update.message and not replay:
            sequence += 1
            update = update.model_copy(
                update={"message": update.message.model_copy(update={"message_id": sequence})}
            )
        before = len(telegram.calls)
        actor = (
            update.callback_query.from_user.id
            if update.callback_query
            else update.message.from_user.id
        )
        await dispatcher.feed_update(telegram.bot, update)
        for method in telegram.calls[before:]:
            if getattr(method, "chat_id", None) is not None:
                assert method.chat_id == actor
            if getattr(method, "text", None):
                assert len(method.text.encode("utf-16-le")) // 2 < 4096

    try:
        await feed(message_update("/paysupport"))
        assert "Stars" in telegram.calls[-1].text
        await feed(message_update("/terms"))
        assert "FAKE TEST TERMS <&>" in telegram.calls[-1].text
        await feed(callback_update(f"sp:order:{order.id}", actor=102))
        assert order.title not in telegram.calls[-1].text
        await feed(message_update(f"/ticket PAYMENT {order.id}\nFAKE PRIVATE CUSTOMER QUESTION"))
        ticket_callback = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
        ticket_id = UUID(ticket_callback.split(":")[2])
        await feed(callback_update(ticket_callback, actor=102))
        assert "FAKE PRIVATE" not in telegram.calls[-1].text
        await feed(callback_update(f"st:view:{ticket_id}:0", actor=200))
        assert "FAKE PRIVATE" not in telegram.calls[-1].text
        await feed(message_update(f"/ticket_assign {ticket_id} 200", actor=9001))
        assert str(ticket_id) in telegram.calls[-1].text
        assert "200" in telegram.calls[-1].text
        confirmation = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
        await feed(callback_update(confirmation, actor=200))
        assert "không có quyền" in telegram.calls[-1].text
        await feed(callback_update(confirmation, actor=9001))
        await feed(message_update("/admin", actor=200))
        assert "st:list:0" in str(telegram.calls[-1].reply_markup)
        await feed(callback_update(f"st:view:{ticket_id}:0", actor=200))
        assert "FAKE PRIVATE CUSTOMER" in telegram.calls[-1].text
        assert order.title in telegram.calls[-1].text
        assert telegram.calls[-1].parse_mode is None
        assert telegram.calls[-1].link_preview_options.is_disabled
        await feed(
            message_update(f"/staff_reply {ticket_id}\nFAKE PRIVATE STAFF ANSWER", actor=200)
        )
        assert "Đã lưu" in telegram.calls[-1].text
        await feed(callback_update(ticket_callback))
        assert "FAKE PRIVATE STAFF ANSWER" in telegram.calls[-1].text
        await feed(callback_update(f"st:view:{ticket_id}:0", actor=201))
        assert "FAKE PRIVATE" not in telegram.calls[-1].text
        await feed(callback_update(f"sp:close:{ticket_id}"))
        close = telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
        await feed(callback_update(close))
        await feed(callback_update(close))
        assert "trước đó" in telegram.calls[-1].text
        assert "FAKE PRIVATE" not in caplog.text
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT state FROM support_tickets")) == "CLOSED"
            assert await connection.scalar(text("SELECT count(*) FROM support_messages")) == 2
            assert await connection.scalar(text("SELECT status FROM orders")) == "PAID"
            assert await connection.scalar(text("SELECT state FROM fulfillments")) == "READY"
    finally:
        await telegram.bot.session.close()
