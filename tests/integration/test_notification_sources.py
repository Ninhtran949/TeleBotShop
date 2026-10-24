from datetime import timedelta
from functools import partial
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.application.services.notifications import NotificationService
from telebotshop.domain.models.fulfillment import DeliveryCode, DeliveryResult, DeliveryState
from telebotshop.domain.models.notifications import NotificationKind
from telebotshop.domain.models.refunds import RefundResult, RefundState
from telebotshop.domain.models.support import TicketKind
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import fulfillment_service, paid_work, timed_uow
from tests.integration.test_notification_failures import Sender
from tests.manual import waiting_manual
from tests.orders import order_service, setup_orders
from tests.payments import payment_service, receipt
from tests.refunds import refund_service
from tests.support import support_service


@pytest.mark.asyncio
async def test_support_notifications_contain_only_metadata_and_current_thread_recipient(
    migrated_database,
):
    engine = migrated_database
    await setup_orders(engine)
    support = support_service(engine)
    ticket = await support.create(101, TicketKind.PAYMENT, "FAKE_PRIVATE_customer_body", uuid4())
    sender = Sender(engine)
    service = NotificationService(partial(unit_of_work, engine), sender, threshold=5)
    await service.discover()
    await support.reply(9001, ticket.id, "FAKE_PRIVATE_staff_body", uuid4(), staff=True)
    await service.discover()
    while await service.run_once():
        pass
    replies = [r for r in sender.requests if r.kind == NotificationKind.TICKET_REPLY]
    assert len(replies) == 1 and replies[0].telegram_id == 101
    assert replies[0].target_id == ticket.id
    assert not any(r.kind == NotificationKind.TICKET_ACTIVITY for r in sender.requests)
    async with engine.connect() as c:
        rows = await c.scalar(text("SELECT json_agg(n)::text FROM notifications n"))
        assert "FAKE_PRIVATE" not in rows and "ciphertext" not in rows
        assert (
            await c.scalar(text("SELECT state FROM notifications WHERE kind='TICKET_ACTIVITY'"))
            == "SUPPRESSED"
        )


@pytest.mark.asyncio
async def test_backlog_is_discovered_with_bounded_controlled_database_time(migrated_database):
    engine = migrated_database
    order, _ = await paid_work(engine)
    async with engine.connect() as c:
        now = await c.scalar(text("SELECT clock_timestamp()"))
    sender = Sender(engine)
    service = NotificationService(
        timed_uow(engine, now + timedelta(minutes=6)), sender, threshold=5
    )
    await service.discover()
    while await service.run_once():
        pass
    backlog = [r for r in sender.requests if r.kind == NotificationKind.BACKLOG]
    assert len(backlog) == 1 and backlog[0].target_id == order.id
    assert backlog[0].telegram_id == 9001


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        NotificationKind.PAYMENT_REVIEW,
        NotificationKind.PAID_ATTENTION,
        NotificationKind.DELIVERY_ATTENTION,
        NotificationKind.MANUAL_WAITING,
        NotificationKind.REFUND_UNCERTAIN,
    ],
)
async def test_financial_alerts_follow_durable_current_source_and_owner(migrated_database, kind):
    engine = migrated_database
    if kind == NotificationKind.MANUAL_WAITING:
        _, expected_id = await waiting_manual(engine)
    elif kind == NotificationKind.PAID_ATTENTION:
        product = await setup_orders(engine)
        order = await order_service(engine).create(101, product.id, uuid4())
        await order_service(engine).cancel(101, order.id)
        await payment_service(engine).record(receipt(order))
        expected_id = order.id
    else:
        order, _ = await paid_work(engine)
        if kind == NotificationKind.PAYMENT_REVIEW:
            await payment_service(engine).record(receipt(order, charge="FAKE_extra_receipt"))
            async with engine.connect() as c:
                expected_id = await c.scalar(
                    text("SELECT id FROM payments WHERE status='REQUIRES_REVIEW'")
                )
        elif kind == NotificationKind.DELIVERY_ATTENTION:
            delivery = AsyncMock()
            delivery.send.return_value = DeliveryResult(
                DeliveryState.UNCERTAIN, code=DeliveryCode.NETWORK
            )
            await fulfillment_service(engine, delivery).run_once()
            expected_id = order.id
        else:
            async with engine.connect() as c:
                expected_id = await c.scalar(text("SELECT id FROM payments"))
            refunds = refund_service(engine)
            refunds.gateway.refund.return_value = RefundResult(RefundState.UNCERTAIN)
            pending = await refunds.prepare(9001, expected_id, "FAKE private refund decision")
            await refunds.confirm(9001, pending.id)
    sender = Sender(engine)
    service = NotificationService(partial(unit_of_work, engine), sender, threshold=5)
    await service.discover()
    for _ in range(20):
        if not await service.run_once():
            break
    selected = [request for request in sender.requests if request.kind == kind]
    assert len(selected) == 1 and selected[0].target_id == expected_id
    assert selected[0].telegram_id == 9001
    assert await service.discover() == 0
    async with engine.connect() as c:
        snapshot = await c.scalar(text("SELECT json_agg(n)::text FROM notifications n"))
        assert "FAKE" not in snapshot and "ciphertext" not in snapshot
