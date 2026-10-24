import asyncio
from dataclasses import replace
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.application.services.checkout import CheckoutService
from telebotshop.application.services.notifications import NotificationService
from telebotshop.domain.models.notifications import (
    NotificationKind,
    NotificationResult,
    NotificationState,
)
from telebotshop.domain.models.orders import InvoiceDelivery, InvoiceStatus
from telebotshop.domain.models.support import PublicTerms
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.orders import setup_orders


@pytest.mark.asyncio
async def test_notification_discovery_is_durable_deduplicated_and_committed_before_send(
    migrated_database,
):
    engine = migrated_database
    product = await setup_orders(engine)
    calls = []

    class Sender:
        async def send(self, request):
            assert engine.pool.checkedout() == 0
            assert request.kind == NotificationKind.LOW_STOCK
            assert request.target_id == product.id and request.telegram_id == 9001
            async with engine.connect() as c:
                assert await c.scalar(text("SELECT state FROM notifications")) == "SENDING"
            calls.append(request)
            return NotificationResult(NotificationState.SENT, 123)

    service = NotificationService(partial(unit_of_work, engine), Sender(), threshold=5)
    await asyncio.gather(service.discover(), service.discover())
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM notifications")) == 1
    assert await service.run_once()
    assert not await service.run_once()
    await service.discover()
    assert not await service.run_once() and len(calls) == 1
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM notifications")) == "SENT"


@pytest.mark.asyncio
async def test_uncertain_notification_never_automatically_resends_after_restart(migrated_database):
    engine = migrated_database
    await setup_orders(engine)
    calls = []

    class Sender:
        async def send(self, request):
            calls.append(request)
            return NotificationResult(NotificationState.UNCERTAIN)

    factory = partial(unit_of_work, engine)
    service = NotificationService(factory, Sender(), threshold=5)
    await service.discover()
    assert await service.run_once()
    restarted = NotificationService(factory, Sender(), threshold=5)
    await restarted.discover()
    assert not await restarted.run_once() and len(calls) == 1
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM notifications")) == "UNCERTAIN"


@pytest.mark.asyncio
async def test_uncertain_invoice_has_an_actionable_owner_notification(migrated_database):
    engine = migrated_database
    product = await setup_orders(engine)
    requests = []

    class InvoiceSender:
        async def send(self, request):
            return InvoiceDelivery(InvoiceStatus.UNCERTAIN)

    class Sender:
        async def send(self, request):
            requests.append(request)
            return NotificationResult(NotificationState.SENT, 123)

    factory = partial(unit_of_work, engine)
    checkout = CheckoutService(factory, InvoiceSender(), PublicTerms("FAKE terms"))
    order = await checkout.prepare(101, product.id, uuid4())
    await checkout.pay(101, order.id, checkout.terms_version)
    service = NotificationService(factory, Sender(), threshold=5)
    await service.discover()
    while await service.run_once():
        pass
    alerts = [r for r in requests if r.kind.value == "INVOICE_UNCERTAIN"]
    assert len(alerts) == 1 and alerts[0].target_id == order.id
    assert alerts[0].telegram_id == 9001


@pytest.mark.asyncio
async def test_notification_result_must_match_original_private_request(migrated_database):
    engine = migrated_database
    await setup_orders(engine)
    requests = []

    class Sender:
        async def send(self, request):
            requests.append(request)
            raise asyncio.CancelledError()

    factory = partial(unit_of_work, engine)
    service = NotificationService(factory, Sender(), threshold=5)
    await service.discover()
    with pytest.raises(asyncio.CancelledError):
        await service.run_once()
    for forged in [replace(requests[0], telegram_id=102), replace(requests[0], target_id=uuid4())]:
        await service.complete(forged, NotificationResult(NotificationState.SENT, 99))
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM notifications")) == "SENDING"
        assert await c.scalar(text("SELECT observed_state FROM notifications")) is None
