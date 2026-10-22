from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from functools import partial
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.fulfillment import DeliveryResult, DeliveryState
from telebotshop.domain.models.identity import CustomerIdentity, Role
from telebotshop.infrastructure.database.uow import SqlAlchemyUnitOfWork, unit_of_work
from tests.fulfillment import timed_uow
from tests.inventory import fake_cipher
from tests.manual import manual_service, waiting_manual
from tests.resends import RecordingSender, delivered_order, resend_service


@pytest.mark.asyncio
async def test_staff_read_grant_does_not_decrypt_or_resend_and_revocation_applies(
    migrated_database,
):
    engine = migrated_database
    order, _, _ = await delivered_order(engine)
    factory = partial(unit_of_work, engine)
    await IdentityService(factory).get_or_create_customer(CustomerIdentity(200, None, "Fake staff"))
    access = AccessService(factory)
    for permission in (Permission.VIEW_ORDERS, Permission.MANAGE_FULFILLMENT):
        action = await access.prepare_permission(9001, 200, Role.ADMIN, permission, True)
        await access.confirm(9001, action.id)
        if permission == Permission.VIEW_ORDERS:
            with pytest.raises(AccessDenied):
                await resend_service(engine, RecordingSender(engine)).prepare(
                    200, order.id, staff=True
                )
    sender, cipher = RecordingSender(engine), Mock(wraps=fake_cipher())
    service = resend_service(engine, sender, cipher=cipher)
    action = await service.prepare(200, order.id, staff=True)
    revoke = await access.prepare_permission(
        9001, 200, Role.ADMIN, Permission.MANAGE_FULFILLMENT, False
    )
    await access.confirm(9001, revoke.id)
    with pytest.raises(AccessDenied):
        await service.confirm(200, action.id)
    with pytest.raises(AccessDenied):
        await service.confirm(9001, action.id)
    cipher.open.assert_not_called()
    assert not sender.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["REFUNDED", "FAILED", "PAID", "FULFILLING"])
async def test_changed_order_state_is_reloaded_before_decrypt(migrated_database, status):
    engine = migrated_database
    order, _, _ = await delivered_order(engine)
    sender, cipher = RecordingSender(engine), Mock(wraps=fake_cipher())
    action = await resend_service(engine, sender).prepare(101, order.id)

    @asynccontextmanager
    async def factory():
        async with unit_of_work(engine) as work:
            original = work.orders.order

            async def changed(order_id, *, for_update=False):
                # Refund persistence arrives in T13; exercise its future repository state here.
                return replace(await original(order_id, for_update=for_update), status=status)

            work.orders.order = changed
            yield work

    service = resend_service(engine, sender, cipher=cipher, factory=factory)
    with pytest.raises(InvalidAction):
        await service.confirm(101, action.id)
    cipher.open.assert_not_called()
    assert not sender.requests


@pytest.mark.asyncio
async def test_manual_completed_order_cannot_repeat_physical_work(migrated_database):
    engine = migrated_database
    order, work_id = await waiting_manual(engine)
    manual = manual_service(engine)
    action = await manual.prepare_completion(9001, work_id, "FAKE done")
    await manual.confirm(9001, action.id)
    with pytest.raises(InvalidAction):
        await resend_service(engine, RecordingSender(engine)).prepare(101, order.id)


@pytest.mark.asyncio
async def test_expiry_and_interrupted_send_are_durable_and_late_ack_does_not_clear_uncertainty(
    migrated_database,
):
    engine = migrated_database
    order, work_id, _ = await delivered_order(engine)
    sender = RecordingSender(engine)
    service = resend_service(engine, sender)
    action = await service.prepare(101, order.id)
    expired = resend_service(engine, sender, factory=timed_uow(engine, action.expires_at))
    assert (await expired.confirm(101, action.id)).status == "CANCELLED"
    action = await service.prepare(101, order.id)
    request = await service.begin(101, action.id)
    restarted = resend_service(
        engine, sender, factory=timed_uow(engine, request.deadline + timedelta(seconds=1))
    )
    assert (await restarted.confirm(101, action.id)).status == "UNCERTAIN"
    assert not sender.requests
    result = DeliveryResult(DeliveryState.SENT, message_id=321)
    assert (await restarted.complete(action.id, result)).status == "UNCERTAIN"
    assert (await restarted.complete(action.id, result)).status == "UNCERTAIN"
    with pytest.raises(InvalidAction):
        await service.prepare(101, order.id)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT status, observed_state, message_id FROM resend_actions WHERE id=:id"),
                {"id": action.id},
            )
        ).one()
        assert tuple(row) == ("UNCERTAIN", "SENT", 321)
        assert await connection.scalar(text("SELECT id FROM fulfillments")) == work_id


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before_send", "after_send", "lost_begin_ack"])
async def test_resend_commit_failures_never_cause_blind_retry(
    migrated_database, monkeypatch, stage
):
    engine = migrated_database
    order, _, _ = await delivered_order(engine)
    sender = RecordingSender(engine)
    service = resend_service(engine, sender)
    action = await service.prepare(101, order.id)
    original_commit = SqlAlchemyUnitOfWork.commit
    calls = 0

    async def fail(self):
        nonlocal calls
        calls += 1
        if stage == "lost_begin_ack":
            await original_commit(self)
            raise OperationalError("fake", {}, OSError("fake lost ACK"))
        if calls == (1 if stage == "before_send" else 2):
            raise OperationalError("fake", {}, OSError("fake commit failure"))
        await original_commit(self)

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyUnitOfWork, "commit", fail)
        with pytest.raises(OperationalError):
            await service.confirm(101, action.id)
    assert len(sender.requests) == (1 if stage == "after_send" else 0)
    if stage == "before_send":
        assert (await service.confirm(101, action.id)).status == "SENT"
        assert len(sender.requests) == 1
    else:
        assert (await service.confirm(101, action.id)).status == "SENDING"
        assert len(sender.requests) == (1 if stage == "after_send" else 0)


@pytest.mark.asyncio
async def test_corrupt_entitlement_fails_durably_without_network(migrated_database):
    engine = migrated_database
    order, _, _ = await delivered_order(engine)
    sender = RecordingSender(engine)
    action = await resend_service(engine, sender).prepare(101, order.id)

    @asynccontextmanager
    async def factory():
        async with unit_of_work(engine) as work:
            original = work.fulfillment.entitlement

            async def corrupt(fulfillment):
                encrypted = await original(fulfillment)
                ciphertext = encrypted.sealed.ciphertext
                return replace(
                    encrypted,
                    sealed=replace(
                        encrypted.sealed, ciphertext=bytes([ciphertext[0] ^ 1]) + ciphertext[1:]
                    ),
                )

            work.fulfillment.entitlement = corrupt
            yield work

    assert (
        await resend_service(engine, sender, factory=factory).confirm(101, action.id)
    ).status == "FAILED"
    assert not sender.requests
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "DELIVERED"
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "SENT"
