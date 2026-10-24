from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.e2e.effects import EffectJournal
from tests.e2e.safety import E2EBlocked


def test_effect_checkpoint_blocks_second_attempt_even_after_restart(tmp_path):
    order = uuid4()
    EffectJournal(tmp_path, order, "payment").begin()
    with pytest.raises(E2EBlocked):
        EffectJournal(tmp_path, order, "payment").begin()


def test_payment_and_refund_have_distinct_checkpoints_but_neither_can_repeat(tmp_path):
    order = uuid4()
    for kind in ("payment", "refund"):
        journal = EffectJournal(tmp_path, order, kind)
        journal.begin()
        journal.confirm()
        with pytest.raises(E2EBlocked):
            journal.begin()


def test_effect_cannot_confirm_without_recorded_attempt(tmp_path):
    with pytest.raises(E2EBlocked):
        EffectJournal(tmp_path, uuid4(), "payment").confirm()


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, True])
async def test_expired_fixture_cannot_bypass_old_payment_fence(tmp_path, monkeypatch, confirmed):
    from tests.e2e import checkout

    old_order = uuid4()
    journal = EffectJournal(tmp_path, old_order, "payment")
    journal.begin()
    if confirmed:
        journal.confirm()
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [old_order]))
    connection = SimpleNamespace(execute=AsyncMock(return_value=result))

    @asynccontextmanager
    async def connect():
        yield connection

    engine = SimpleNamespace(connect=connect)
    client = SimpleNamespace(
        get_entity=AsyncMock(side_effect=AssertionError("new purchase started"))
    )
    monkeypatch.setattr(checkout, "require_fixture", AsyncMock())
    monkeypatch.setattr(checkout, "read_order", AsyncMock(return_value=None))
    with pytest.raises(E2EBlocked, match="earlier payment attempt"):
        await checkout.purchase(
            SimpleNamespace(root=tmp_path),
            "owner",
            client,
            SimpleNamespace(id=42, username="test_bot"),
            SimpleNamespace(id=7),
            engine,
            "T14_KEY",
        )
    client.get_entity.assert_not_called()
