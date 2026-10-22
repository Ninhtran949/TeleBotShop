from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.identity import Role
from telebotshop.domain.models.support import TicketKind
from telebotshop.infrastructure.database.uow import SqlAlchemyUnitOfWork, unit_of_work
from tests.fulfillment import paid_work, timed_uow
from tests.integration.test_support import staff
from tests.inventory import fake_cipher
from tests.support import support_service


@pytest.mark.asyncio
async def test_reassignment_revokes_old_scope_and_target_permission_is_rechecked(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    access = await staff(engine)
    await staff(engine, 201)
    service = support_service(engine)
    ticket = await service.create(101, TicketKind.GENERAL, "FAKE private", uuid4())
    cipher = Mock(wraps=fake_cipher())
    reader = support_service(engine, cipher=cipher)
    action = await service.prepare_assignment(9001, ticket.id, 200)
    revoke = await access.prepare_permission(
        9001, 200, Role.SUPPORT, Permission.MANAGE_TICKETS, False
    )
    await access.confirm(9001, revoke.id)
    with pytest.raises(InvalidAction):
        await service.confirm(9001, action.id)
    grant = await access.prepare_permission(
        9001, 200, Role.SUPPORT, Permission.MANAGE_TICKETS, True
    )
    await access.confirm(9001, grant.id)
    assert await service.confirm(9001, action.id)
    stale = await service.prepare_close(200, ticket.id, staff=True)
    other = await service.prepare_assignment(9001, ticket.id, 201)
    assert await service.confirm(9001, other.id)
    for actor, staff_mode in ((102, False), (200, True)):
        with pytest.raises(AccessDenied):
            await reader.detail(actor, ticket.id, staff=staff_mode)
    with pytest.raises(AccessDenied):
        await reader.confirm(200, stale.id)
    cipher.open.assert_not_called()
    assert (await service.list(200, staff=True)).items == ()


@pytest.mark.asyncio
async def test_cancel_expiry_cross_ticket_keys_and_pagination(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    service = support_service(engine)
    tickets = [
        await service.create(101, TicketKind.GENERAL, "FAKE question", uuid4()) for _ in range(7)
    ]
    first, second = await service.list(101), await service.list(101, 1)
    assert len(first.items) == 5 and first.has_more
    assert len(second.items) == 2 and not second.has_more
    assert {t.id for t in first.items}.isdisjoint(t.id for t in second.items)
    action = await service.prepare_close(101, tickets[0].id)
    await service.cancel(101, action.id)
    await service.cancel(101, action.id)
    with pytest.raises(InvalidAction):
        await service.confirm(101, action.id)
    action = await service.prepare_close(101, tickets[0].id)
    expired = support_service(engine, factory=timed_uow(engine, action.expires_at))
    with pytest.raises(InvalidAction):
        await expired.confirm(101, action.id)
    key = uuid4()
    await service.reply(101, tickets[0].id, "FAKE one", key)
    with pytest.raises(InvalidAction):
        await service.reply(101, tickets[1].id, "FAKE other", key)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["create", "reply", "confirm"])
@pytest.mark.parametrize("lost_ack", [False, True])
async def test_ticket_commit_failure_or_lost_ack_is_atomic_and_replay_safe(
    migrated_database, monkeypatch, stage, lost_ack
):
    engine = migrated_database
    await paid_work(engine)
    service = support_service(engine)
    key = uuid4()
    ticket = (
        None
        if stage == "create"
        else await service.create(101, TicketKind.GENERAL, "FAKE first", uuid4())
    )
    action = await service.prepare_close(101, ticket.id) if stage == "confirm" else None
    original = SqlAlchemyUnitOfWork.commit

    async def operation():
        if stage == "create":
            return await service.create(101, TicketKind.GENERAL, "FAKE first", key)
        if stage == "reply":
            return await service.reply(101, ticket.id, "FAKE reply", key)
        return await service.confirm(101, action.id)

    async def fail(self):
        if lost_ack:
            await original(self)
        raise OperationalError("fake", {}, OSError("FAKE lost commit ACK"))

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyUnitOfWork, "commit", fail)
        with pytest.raises(OperationalError):
            await operation()
    result = await operation()
    if stage == "confirm":
        assert result is (not lost_ack)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM support_tickets")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM support_messages")) == (
            2 if stage == "reply" else 1
        )
        assert await connection.scalar(text("SELECT state FROM support_tickets")) == (
            "CLOSED" if stage == "confirm" else "OPEN"
        )


@pytest.mark.asyncio
async def test_ticket_audit_failure_and_ciphertext_tamper_return_no_private_body(
    migrated_database, monkeypatch
):
    engine = migrated_database
    await paid_work(engine)
    service = support_service(engine)
    ticket = await service.create(101, TicketKind.PAYMENT, "FAKE private card-like body", uuid4())

    @asynccontextmanager
    async def factory():
        async with unit_of_work(engine) as work:
            original = work.support.messages

            async def corrupted(ticket_id, page):
                rows = await original(ticket_id, page)
                post = rows.items[0]
                data = post.sealed.ciphertext
                sealed = replace(post.sealed, ciphertext=bytes([data[0] ^ 1]) + data[1:])
                return replace(rows, items=(replace(post, sealed=sealed),))

            work.support.messages = corrupted
            yield work

    from telebotshop.domain.models.inventory import InventoryCryptoError

    with pytest.raises(InventoryCryptoError):
        await support_service(engine, factory=factory).detail(101, ticket.id)
    from telebotshop.infrastructure.database.repositories.support import SqlSupportRepository

    async def audit_failure(*args, **kwargs):
        raise OperationalError("fake", {}, OSError("FAKE audit unavailable"))

    with monkeypatch.context() as patch:
        patch.setattr(SqlSupportRepository, "audit", audit_failure)
        with pytest.raises(OperationalError):
            await service.reply(101, ticket.id, "FAKE must roll back", uuid4())
        with pytest.raises(OperationalError):
            await service.detail(101, ticket.id)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM support_messages")) == 1
        assert await connection.scalar(text("SELECT version FROM support_tickets")) == 1


@pytest.mark.asyncio
async def test_ticket_sql_history_identity_and_atomic_close_guards(migrated_database):
    engine = migrated_database
    order, _ = await paid_work(engine)
    service = support_service(engine)
    ticket = await service.create(
        101, TicketKind.PAYMENT, "FAKE initial", uuid4(), order_id=order.id
    )
    action = await service.prepare_close(101, ticket.id)
    for sql in (
        "UPDATE support_tickets SET customer_id=(SELECT id FROM users WHERE telegram_id=102)",
        "UPDATE support_tickets SET state='CLOSED',closed_at=clock_timestamp(),version=version+1",
        "UPDATE support_messages SET staff=true",
        "DELETE FROM support_messages",
        "TRUNCATE support_messages",
        "TRUNCATE support_tickets CASCADE",
        "UPDATE support_actions SET expected_version=99",
        "UPDATE support_actions SET status='applied',completed_at=clock_timestamp()",
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(text(sql))
    assert await service.confirm(101, action.id)
    assert (await service.detail(101, ticket.id)).ticket.state == "CLOSED"
