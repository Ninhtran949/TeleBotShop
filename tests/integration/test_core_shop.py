from dataclasses import replace
from functools import partial
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from aiogram.methods import AnswerPreCheckoutQuery, RefundStarPayment, SendDocument, SendInvoice
from aiogram.types import Chat, Message
from sqlalchemy import text

from telebotshop.application.services.notifications import NotificationService
from telebotshop.bootstrap import runtime_dispatcher
from telebotshop.domain.models.catalog import LicenseMetadata, ProductType, SaleState
from telebotshop.domain.models.inventory import ImportFormat
from telebotshop.domain.models.support import PublicTerms
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.delivery.finite import FiniteDeliverySender
from telebotshop.infrastructure.telegram.notifications import TelegramNotificationSender
from tests.catalog import fake_product, setup_catalog
from tests.fulfillment import fulfillment_service
from tests.inventory import fake_cipher, inventory_service
from tests.orders import order_service, setup_orders
from tests.resources import FILE_ID, resource_product
from tests.telegram import (
    TelegramRecorder,
    callback_update,
    message_update,
    payment_update,
    pre_checkout_update,
)


async def product_fixture(engine, kind, document):
    if kind == ProductType.UNIQUE_KEY:
        return await setup_orders(engine)
    if kind != ProductType.LICENSE:
        product, _ = await resource_product(engine, kind, document=document)
        return product
    catalog = await setup_catalog(engine)
    draft = replace(fake_product(), product_type=kind, metadata=LicenseMetadata("FAKE activation"))
    action = await catalog.prepare_product(9001, draft)
    await catalog.confirm(9001, action.id)
    action = await catalog.prepare_sale(9001, draft.sku, SaleState.ACTIVE)
    await catalog.confirm(9001, action.id)
    inventory = inventory_service(engine)
    action = await inventory.prepare_import(9001, draft.sku, b"FAKE-license-0", ImportFormat.TXT)
    await inventory.confirm(9001, action.id)
    return await catalog.admin_product(9001, draft.sku)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,document",
    [(kind, False) for kind in ProductType] + [(ProductType.REUSABLE_RESOURCE, True)],
)
async def test_public_checkout_to_delivery_support_and_refund_uses_shared_runtime(
    migrated_database, monkeypatch, caplog, kind, document
):
    engine = migrated_database
    product = await product_fixture(engine, kind, document)
    factory = partial(unit_of_work, engine)
    telegram = TelegramRecorder(monkeypatch)

    async def send(bot, method, timeout=None):
        assert engine.pool.checkedout() == 0
        if isinstance(method, RefundStarPayment):
            telegram.calls.append(method)
            return True
        result = await telegram.send(bot, method, timeout)
        if isinstance(result, Message):
            result = result.model_copy(
                update={"chat": Chat(id=method.chat_id, type="private")}
            ).as_(bot)
        return result

    monkeypatch.setattr(telegram.bot.session, "make_request", AsyncMock(side_effect=send))
    clock = {"now": 0}
    monkeypatch.setattr("telebotshop.infrastructure.telegram.guard.monotonic", lambda: clock["now"])
    dispatcher = runtime_dispatcher(
        factory, inventory_service(engine), fake_cipher(), telegram.bot, PublicTerms("FAKE terms")
    )
    sequence = 100

    async def feed(update):
        nonlocal sequence
        sequence += 1
        clock["now"] += 11
        update = update.model_copy(update={"update_id": sequence})
        if update.message:
            update = update.model_copy(
                update={"message": update.message.model_copy(update={"message_id": sequence})}
            )
        await dispatcher.feed_update(telegram.bot, update)

    def view():
        return next(call for call in reversed(telegram.calls) if getattr(call, "text", None))

    def button(prefix):
        return next(
            button.callback_data
            for row in view().reply_markup.inline_keyboard
            for button in row
            if (button.callback_data or "").startswith(prefix)
        )

    try:
        await feed(message_update("/start"))
        await feed(message_update("/start", actor=102))
        await feed(message_update("/admin", actor=9001))
        callbacks = str(view().reply_markup)
        for expected in ("pc:list", "iv:home", "ord:a", "ord:k", "st:list", "rf:list", "mf:queue"):
            assert expected in callbacks
        await feed(message_update("/admin"))
        assert "không có quyền" in view().text
        await feed(message_update("/search Sản phẩm"))
        await feed(callback_update(button("shop:p:")))
        buy = button("buy:")
        await feed(callback_update(buy))
        pay = button("ck:p:")
        order_id = UUID(pay.split(":")[2])
        await feed(callback_update(buy))
        assert UUID(button("ck:p:").split(":")[2]) == order_id
        await feed(message_update(f"/price {product.draft.sku} 333", actor=9001))
        await feed(callback_update(button("pc:confirm:"), actor=9001))
        await feed(callback_update(pay, actor=102))
        assert not any(isinstance(c, SendInvoice) for c in telegram.calls)
        await feed(callback_update(pay))
        await feed(callback_update(pay))
        invoices = [c for c in telegram.calls if isinstance(c, SendInvoice)]
        assert len(invoices) == 1
        invoice = invoices[0]
        assert invoice.chat_id == 101 and invoice.currency == "XTR" and invoice.provider_token == ""
        assert invoice.prices[0].amount == product.draft.price_stars == 100
        order = await order_service(engine).get(101, order_id)
        await feed(pre_checkout_update(payload=order.payload))
        answers = [c for c in telegram.calls if isinstance(c, AnswerPreCheckoutQuery)]
        assert len(answers) == 1 and answers[0].ok
        receipt = payment_update(payload=order.payload, charge="FAKE_CORE_CHARGE")
        await feed(receipt)
        await feed(receipt)
        async with engine.connect() as c:
            assert await c.scalar(text("SELECT count(*) FROM orders")) == 1
            assert await c.scalar(text("SELECT count(*) FROM payments WHERE status='MATCHED'")) == 1
            assert await c.scalar(text("SELECT count(*) FROM fulfillments")) == 1
            work_id = await c.scalar(text("SELECT id FROM fulfillments"))
            payment_id = await c.scalar(text("SELECT id FROM payments"))
        before = len(telegram.calls)
        worker = fulfillment_service(engine, FiniteDeliverySender(telegram.bot))
        assert await worker.run_once() and not await worker.run_once()
        deliveries = telegram.calls[before:]
        assert len(deliveries) == 1 and deliveries[0].chat_id == 101
        if kind == ProductType.DIGITAL_FILE or document:
            assert isinstance(deliveries[0], SendDocument) and deliveries[0].document == FILE_ID
        if kind == ProductType.MANUAL_FULFILLMENT:
            assert "staff instructions" not in deliveries[0].text
            await feed(message_update(f"/manual_complete {work_id}\nFAKE evidence", actor=9001))
            complete = button("mf:confirm:")
            await feed(callback_update(complete, actor=9001))
            await feed(callback_update(complete, actor=9001))
        await feed(message_update(f"/orders {order.id}"))
        assert "Đã giao" in view().text and "100 Stars" in view().text
        await feed(message_update(f"/orders {order.id}", actor=102))
        assert str(order.id) not in view().text
        await feed(message_update("/customer 101", actor=9001))
        assert str(order.id) in str(view().reply_markup)
        await feed(message_update(f"/ticket PAYMENT {order.id}\nFAKE PRIVATE QUESTION"))
        ticket_id = button("sp:view:").split(":")[2]
        await feed(message_update(f"/staff_reply {ticket_id}\nFAKE PRIVATE ANSWER", actor=9001))
        notifier = NotificationService(
            factory, TelegramNotificationSender(telegram.bot), threshold=5
        )
        await notifier.discover()
        before = len(telegram.calls)
        for _ in range(20):
            if not await notifier.run_once():
                break
        notices = telegram.calls[before:]
        reply = next(c for c in notices if c.chat_id == 101)
        assert ticket_id in reply.text and "FAKE PRIVATE" not in reply.text
        await feed(message_update(f"/refund {payment_id}\nFAKE decision", actor=9001))
        refund = button("rf:confirm:")
        await feed(callback_update(refund, actor=101))
        assert not any(isinstance(c, RefundStarPayment) for c in telegram.calls)
        await feed(callback_update(refund, actor=9001))
        await feed(callback_update(refund, actor=9001))
        refunds = [c for c in telegram.calls if isinstance(c, RefundStarPayment)]
        assert len(refunds) == 1 and refunds[0].user_id == 101
        assert refunds[0].telegram_payment_charge_id == "FAKE_CORE_CHARGE"
        await feed(message_update(f"/orders {order.id}"))
        assert "Đã hoàn tiền" in view().text and "ord:r:" not in str(view().reply_markup)
        async with engine.connect() as c:
            assert await c.scalar(text("SELECT count(*) FROM delivery_attempts")) == 1
            assert await c.scalar(text("SELECT count(*) FROM payments")) == 1
            assert await c.scalar(text("SELECT hold FROM fulfillments")) is True
            if kind in {ProductType.UNIQUE_KEY, ProductType.LICENSE}:
                assert await c.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"
            else:
                assert await c.scalar(text("SELECT count(*) FROM order_resources")) == 1
        for secret in ("FAKE_CORE_CHARGE", "FAKE PRIVATE", "FAKE decision", order.payload, FILE_ID):
            assert secret not in caplog.text
    finally:
        await telegram.bot.session.close()
