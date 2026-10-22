from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text

from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.identity import Role
from telebotshop.domain.models.refunds import RefundResult, RefundState
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import fulfillment_service, paid_work, timed_uow
from tests.inventory import fake_cipher
from tests.refunds import refund_service, refund_staff


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["prepare", "begin", "complete"])
@pytest.mark.parametrize("lost_ack", [False, True])
async def test_refund_commit_failure_and_lost_ack_never_repeat_external_effect(
    migrated_database, stage, lost_ack
):
    engine = migrated_database
    await paid_work(engine)
    service = refund_service(engine)
    normal = service.uow
    async with engine.connect() as connection:
        payment = await connection.scalar(text("SELECT id FROM payments"))
    action = await service.prepare(9001, payment, "FAKE-decision") if stage != "prepare" else None
    if stage == "complete":
        await service.begin(9001, action.id)

    @asynccontextmanager
    async def broken():
        async with unit_of_work(engine) as work:
            commit = work.commit

            async def fail():
                if lost_ack:
                    await commit()
                raise RuntimeError("FAKE-commit-failure")

            work.commit = fail
            yield work

    service.uow = broken
    with pytest.raises(RuntimeError, match="FAKE-commit-failure"):
        if stage == "prepare":
            await service.prepare(9001, payment, "FAKE-decision")
        elif stage == "begin":
            await service.begin(9001, action.id)
        else:
            await service.complete(action.id, RefundResult(RefundState.SUCCEEDED))
    service.uow = normal
    service.gateway.refund.assert_not_awaited()
    async with engine.connect() as connection:
        status = await connection.scalar(text("SELECT status FROM refund_intents"))
        held = await connection.scalar(text("SELECT hold FROM fulfillments"))
        expected = (
            ("PENDING" if lost_ack else None)
            if stage == "prepare"
            else ("SENDING" if lost_ack else "PENDING")
            if stage == "begin"
            else ("SUCCEEDED" if lost_ack else "SENDING")
        )
        assert status == expected
        assert held == (stage == "complete" or stage == "begin" and lost_ack)
    if stage == "prepare":
        action = await service.prepare(9001, payment, "FAKE-decision")
    if stage == "begin" and lost_ack or stage == "complete":
        await service.confirm(9001, action.id)
        service.gateway.refund.assert_not_awaited()
    if stage == "complete":
        await service.complete(action.id, RefundResult(RefundState.SUCCEEDED))
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_intents")) == 1
        assert await connection.scalar(text("SELECT status FROM inventory_units")) == "ALLOCATED"


@pytest.mark.asyncio
async def test_refund_actor_grant_expiry_cancel_and_stale_work_are_rechecked_before_decrypt(
    migrated_database,
):
    engine = migrated_database
    await paid_work(engine)
    access = await refund_staff(engine, 202)
    await refund_staff(engine, 203)
    service = refund_service(engine)
    service.cipher = Mock(wraps=fake_cipher())
    async with engine.connect() as connection:
        payment = await connection.scalar(text("SELECT id FROM payments"))
    action = await service.prepare(202, payment, "FAKE-decision")
    with pytest.raises(InvalidAction):
        await service.confirm(203, action.id)
    revoke = await access.prepare_permission(9001, 202, Role.ADMIN, Permission.REFUND, False)
    await access.confirm(9001, revoke.id)
    with pytest.raises(AccessDenied):
        await service.confirm(202, action.id)
    grant = await access.prepare_permission(9001, 202, Role.ADMIN, Permission.REFUND, True)
    await access.confirm(9001, grant.id)
    normal = service.uow
    service.uow = timed_uow(engine, action.expires_at + timedelta(seconds=1))
    with pytest.raises(InvalidAction):
        await service.confirm(202, action.id)
    service.uow = normal
    await service.cancel(202, action.id)
    assert (await service.confirm(202, action.id)).status == RefundState.CANCELLED
    stale = await service.prepare(202, payment, "FAKE-new-decision")
    assert await fulfillment_service(engine, AsyncMock()).claim()
    with pytest.raises(InvalidAction):
        await service.confirm(202, stale.id)
    service.cipher.open.assert_not_called()
    service.gateway.refund.assert_not_awaited()


@pytest.mark.asyncio
async def test_verification_rechecks_grant_after_network_and_keeps_missing_evidence_unverified(
    migrated_database,
):
    from dataclasses import replace
    from datetime import UTC, datetime

    from telebotshop.domain.models.refunds import StarEvidence
    from tests.payments import payment_service, receipt

    engine = migrated_database
    order, _ = await paid_work(engine)
    access = await refund_staff(engine)
    extra = replace(receipt(order), charge_id="FAKE-extra", payer_id=None)
    payment = await payment_service(engine).record(extra)
    service = refund_service(engine)

    async def fetch(offset):
        assert engine.pool.checkedout() == 0
        revoke = await access.prepare_permission(9001, 202, Role.ADMIN, Permission.REFUND, False)
        await access.confirm(9001, revoke.id)
        return (
            StarEvidence(
                extra.charge_id, 101, extra.amount, extra.payload, False, datetime.now(UTC)
            ),
        )

    service.gateway.transactions.side_effect = fetch
    with pytest.raises(AccessDenied):
        await service.verify(202, payment.payment_id)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM payment_verifications")) == 0
    service.gateway.transactions.side_effect = None
    service.gateway.transactions.return_value = ()
    assert await service.verify(9001, payment.payment_id) is False
