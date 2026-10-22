import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from telebotshop.application.services.manual import ManualFulfillmentService
from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.infrastructure.database.uow import SqlAlchemyUnitOfWork, unit_of_work
from tests.fulfillment import timed_uow
from tests.inventory import fake_cipher
from tests.manual import manual_service, waiting_manual


@pytest.mark.asyncio
@pytest.mark.parametrize("same_action", [True, False])
async def test_two_connections_cannot_complete_manual_work_twice(migrated_database, same_action):
    engine = migrated_database
    _, work_id = await waiting_manual(engine)
    service = manual_service(engine)
    first = await service.prepare_completion(9001, work_id, "FAKE evidence 1")
    second = (
        first if same_action else await service.prepare_completion(9001, work_id, "FAKE evidence 2")
    )
    outcomes = await asyncio.gather(
        service.confirm(9001, first.id), service.confirm(9001, second.id), return_exceptions=True
    )
    assert sum(outcome is True for outcome in outcomes) == 1
    if same_action:
        assert sum(outcome is False for outcome in outcomes) == 1
    else:
        assert sum(isinstance(outcome, InvalidAction) for outcome in outcomes) == 1
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM manual_actions WHERE status = 'applied'")
            )
            == 1
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action = 'manual.completed'")
            )
            == 1
        )
        assert await connection.scalar(text("SELECT count(*) FROM fulfillments")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["hold", "expired", "cancelled", "wrong_actor"])
async def test_manual_confirmation_blocks_changed_or_expired_action_before_decrypt(
    migrated_database, blocked
):
    engine = migrated_database
    _, work_id = await waiting_manual(engine)
    service = manual_service(engine)
    if blocked == "expired":
        service = ManualFulfillmentService(
            timed_uow(engine, datetime.now(UTC) - timedelta(minutes=20)), fake_cipher()
        )
    action = await service.prepare_completion(9001, work_id, "FAKE evidence")
    if blocked == "hold":
        async with engine.begin() as connection:
            await connection.execute(text("SELECT id FROM orders FOR UPDATE"))
            await connection.execute(text("UPDATE fulfillments SET hold = true"))
    elif blocked == "cancelled":
        await service.cancel(9001, action.id)
    cipher = Mock(wraps=fake_cipher())
    check = ManualFulfillmentService(manual_service(engine).uow, cipher)
    with pytest.raises(AccessDenied if blocked == "wrong_actor" else InvalidAction):
        await check.confirm(101 if blocked == "wrong_actor" else 9001, action.id)
    cipher.open.assert_not_called()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == "FULFILLING"
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "AWAITING_MANUAL"


@pytest.mark.asyncio
@pytest.mark.parametrize("after_commit", [False, True])
async def test_manual_completion_commit_failure_or_lost_ack_has_safe_replay(
    migrated_database, monkeypatch, after_commit
):
    engine = migrated_database
    _, work_id = await waiting_manual(engine)
    service = manual_service(engine)
    action = await service.prepare_completion(9001, work_id, "FAKE evidence")
    original = SqlAlchemyUnitOfWork.commit

    async def fail(self):
        if after_commit:
            await original(self)
        raise OperationalError("fake", {}, OSError("FAKE private DSN"))

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyUnitOfWork, "commit", fail)
        with pytest.raises(OperationalError):
            await service.confirm(9001, action.id)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM orders")) == (
            "DELIVERED" if after_commit else "FULFILLING"
        )
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == (
            "SENT" if after_commit else "AWAITING_MANUAL"
        )
    assert await service.confirm(9001, action.id) is (not after_commit)
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action = 'manual.completed'")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_manual_evidence_tamper_is_rejected_without_state_change_or_plaintext_log(
    migrated_database, caplog
):
    engine = migrated_database
    _, work_id = await waiting_manual(engine)
    action = await manual_service(engine).prepare_completion(9001, work_id, "FAKE private evidence")

    @asynccontextmanager
    async def factory():
        async with unit_of_work(engine) as work:
            original = work.manual.evidence

            async def corrupt(action_id):
                value = await original(action_id)
                return replace(
                    value, ciphertext=value.ciphertext[:-1] + bytes([value.ciphertext[-1] ^ 1])
                )

            work.manual.evidence = corrupt
            yield work

    with pytest.raises(InventoryCryptoError):
        await ManualFulfillmentService(factory, fake_cipher()).confirm(9001, action.id)
    assert "private evidence" not in caplog.text
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM manual_actions")) == "pending"
        assert await connection.scalar(text("SELECT state FROM fulfillments")) == "AWAITING_MANUAL"
