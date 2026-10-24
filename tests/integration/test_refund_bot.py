from functools import partial
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import text

from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.history import HistoryService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.payments import PaymentService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.bootstrap import create_dispatcher
from telebotshop.domain.models.refunds import RefundResult, RefundState
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.checkout import checkout_service
from tests.fulfillment import paid_work
from tests.inventory import inventory_service
from tests.manual import manual_service
from tests.refunds import refund_service, refund_staff
from tests.resends import RecordingSender, resend_service
from tests.resources import resource_service
from tests.support import support_service
from tests.telegram import TelegramRecorder, callback_update, message_update, refund_update


@pytest.mark.asyncio
async def test_native_refund_navigation_current_grants_and_event_without_network_transaction(
    migrated_database, monkeypatch, caplog
):
    engine = migrated_database
    order, _ = await paid_work(engine)
    access = await refund_staff(engine)
    factory = partial(unit_of_work, engine)
    service = refund_service(engine)
    telegram = TelegramRecorder(monkeypatch)
    clock = {"now": 0}
    monkeypatch.setattr("telebotshop.infrastructure.telegram.guard.monotonic", lambda: clock["now"])

    async def send(bot, method, timeout=None):
        assert engine.pool.checkedout() == 0
        if getattr(method, "text", None):
            assert len(method.text.encode("utf-16-le")) // 2 < 4096
            assert "FAKE-private-refund-reference" not in method.text
            assert "FAKE-charge-1" not in method.text
        return await telegram.send(bot, method, timeout)

    monkeypatch.setattr(telegram.bot.session, "make_request", AsyncMock(side_effect=send))
    dispatcher = create_dispatcher(
        IdentityService(factory),
        access,
        CatalogService(factory),
        inventory_service(engine),
        PreCheckoutService(factory),
        PaymentService(factory),
        resources=resource_service(engine),
        manual=manual_service(engine),
        history=HistoryService(factory),
        resends=resend_service(engine, RecordingSender(engine)),
        support=support_service(engine),
        refunds=service,
        checkout=checkout_service(engine),
    )
    async with engine.connect() as connection:
        payment_id = await connection.scalar(text("SELECT id FROM payments"))

    async def feed(update):
        clock["now"] += 11
        return await dispatcher.feed_update(telegram.bot, update)

    try:
        await feed(message_update("/admin", actor=202))
        assert "rf:list:0" in str(telegram.calls[-1].reply_markup)
        await feed(callback_update("rf:list:0", actor=202))
        assert str(payment_id) in str(telegram.calls[-1].reply_markup)
        await feed(
            message_update(f"/refund {payment_id}\nFAKE-private-refund-reference", actor=101)
        )
        service.gateway.refund.assert_not_awaited()
        await feed(
            message_update(f"/refund {payment_id}\nFAKE-private-refund-reference", actor=202)
        )
        token = UUID(
            telegram.calls[-1].reply_markup.inline_keyboard[0][0].callback_data.split(":")[2]
        )
        from telebotshop.domain.models.access import Permission
        from telebotshop.domain.models.identity import Role

        revoke = await access.prepare_permission(9001, 202, Role.ADMIN, Permission.REFUND, False)
        await access.confirm(9001, revoke.id)
        await feed(callback_update(f"rf:confirm:{token}", actor=202))
        service.gateway.refund.assert_not_awaited()
        grant = await access.prepare_permission(9001, 202, Role.ADMIN, Permission.REFUND, True)
        await access.confirm(9001, grant.id)

        async def refund(request):
            assert engine.pool.checkedout() == 0
            return RefundResult(RefundState.SUCCEEDED)

        service.gateway.refund.side_effect = refund
        await feed(callback_update(f"rf:confirm:{token}", actor=202))
        await feed(callback_update(f"rf:confirm:{token}", actor=202))
        service.gateway.refund.assert_awaited_once()
        await feed(
            refund_update(
                payload=order.payload, charge="FAKE-charge-1", amount=order.price_stars, actor=None
            )
        )
        await feed(message_update(f"/orders {order.id}", actor=101))
        assert "Đã hoàn tiền" in telegram.calls[-1].text
        assert "ord:resend" not in str(telegram.calls[-1].reply_markup)
        assert (
            "FAKE-private-refund-reference" not in caplog.text
            and "FAKE-charge-1" not in caplog.text
        )
    finally:
        await telegram.bot.session.close()
