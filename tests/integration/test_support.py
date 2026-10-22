from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.application.services.access import AccessService
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.identity import CustomerIdentity, Role
from telebotshop.domain.models.support import TicketKind
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import paid_work
from tests.support import support_service


async def staff(engine, actor=200, *, role=Role.SUPPORT):
    factory = partial(unit_of_work, engine)
    await IdentityService(factory).get_or_create_customer(
        CustomerIdentity(actor, None, "Fake staff")
    )
    access = AccessService(factory)
    grant = await access.prepare_permission(9001, actor, role, Permission.MANAGE_TICKETS, True)
    await access.confirm(9001, grant.id)
    return access


@pytest.mark.asyncio
async def test_ticket_owns_linked_order_and_immutable_encrypted_idempotent_messages(
    migrated_database,
):
    engine = migrated_database
    order, _ = await paid_work(engine)
    service = support_service(engine)
    with pytest.raises(AccessDenied):
        await service.create(102, TicketKind.PAYMENT, "FAKE private", uuid4(), order_id=order.id)
    request = uuid4()
    ticket = await service.create(
        101, TicketKind.PAYMENT, "FAKE private initial <&>", request, order_id=order.id
    )
    assert (
        await service.create(
            101, TicketKind.PAYMENT, "edited replay ignored", request, order_id=order.id
        )
    ).id == ticket.id
    for method in (service.detail, service.prepare_close):
        with pytest.raises(AccessDenied):
            await method(102, ticket.id)
    assert (await service.list(102)).items == ()
    detail = await service.detail(101, ticket.id)
    assert (
        detail.order.id == order.id and detail.messages.items[0].text == "FAKE private initial <&>"
    )
    reply_key = uuid4()
    post_id = await service.reply(101, ticket.id, "FAKE private follow-up", reply_key)
    assert await service.reply(101, ticket.id, "replay", reply_key) == post_id
    assert (await service.detail(101, ticket.id)).messages.items[0].text == "FAKE private follow-up"
    older = await service.detail(101, ticket.id, page=1)
    assert older.messages.items[0].text == "FAKE private initial <&>"
    async with engine.connect() as connection:
        rows = (
            (await connection.execute(text("SELECT ciphertext FROM support_messages")))
            .scalars()
            .all()
        )
        assert len(rows) == 2 and all(b"FAKE private" not in row for row in rows)
        audit = str((await connection.execute(text("SELECT changes FROM audit_logs"))).all())
        assert "FAKE private" not in audit


@pytest.mark.asyncio
async def test_support_only_sees_assignment_and_current_grant_before_decrypt(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    access = await staff(engine)
    await staff(engine, 201)
    service = support_service(engine)
    ticket = await service.create(101, TicketKind.GENERAL, "FAKE private question", uuid4())
    assert (await service.list(200, staff=True)).items == ()
    with pytest.raises(AccessDenied):
        await service.detail(200, ticket.id, staff=True)
    assignment = await service.prepare_assignment(9001, ticket.id, 200)
    with pytest.raises(AccessDenied):
        await service.confirm(201, assignment.id)
    assert await service.confirm(9001, assignment.id)
    assert not await service.confirm(9001, assignment.id)
    assert (await service.list(200, staff=True)).items[0].id == ticket.id
    assert (await service.detail(200, ticket.id, staff=True)).messages.items[
        0
    ].text == "FAKE private question"
    with pytest.raises(AccessDenied):
        await service.detail(201, ticket.id, staff=True)
    with pytest.raises(AccessDenied):
        await service.prepare_assignment(200, ticket.id, 201)
    await service.reply(200, ticket.id, "FAKE staff answer", uuid4(), staff=True)
    assert (await service.detail(101, ticket.id)).messages.items[0].from_staff
    revoke = await access.prepare_permission(
        9001, 200, Role.SUPPORT, Permission.MANAGE_TICKETS, False
    )
    await access.confirm(9001, revoke.id)
    with pytest.raises(AccessDenied):
        await service.detail(200, ticket.id, staff=True)
    with pytest.raises(AccessDenied):
        await service.reply(200, ticket.id, "no grant", uuid4(), staff=True)


@pytest.mark.asyncio
async def test_confirmed_close_is_stale_after_reply_and_does_not_change_payment(migrated_database):
    engine = migrated_database
    order, _ = await paid_work(engine)
    service = support_service(engine)
    ticket = await service.create(
        101, TicketKind.DELIVERY, "FAKE delivery problem", uuid4(), order_id=order.id
    )
    stale = await service.prepare_close(101, ticket.id)
    await service.reply(101, ticket.id, "FAKE update", uuid4())
    with pytest.raises(InvalidAction):
        await service.confirm(101, stale.id)
    action = await service.prepare_close(101, ticket.id)
    assert await service.confirm(101, action.id)
    assert not await service.confirm(101, action.id)
    with pytest.raises(InvalidAction):
        await service.reply(101, ticket.id, "closed", uuid4())
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM support_tickets")) == "CLOSED"
        assert await connection.scalar(text("SELECT status FROM orders")) == "PAID"
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "READY"
