from dataclasses import replace
from functools import partial
from uuid import uuid4

import pytest

from telebotshop.application.services.topup_review import ManualPaymentReviewService
from telebotshop.domain.models.topups import TopUpState
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.integration.test_topups_v2 import bank_evidence, topup_fixture


@pytest.mark.asyncio
async def test_rejected_settlement_replay_does_not_reopen_or_credit(migrated_database):
    wallet, topups = await topup_fixture(migrated_database)
    intent = await topups.create_bank(101, 1000, uuid4())
    event = replace(bank_evidence(intent), reference="FAKE-BAD-REFERENCE")
    result = await topups.ingest(event)
    reviews = ManualPaymentReviewService(partial(unit_of_work, migrated_database))
    action = uuid4()
    await reviews.prepare(
        9001,
        result.event_id,
        intent.id,
        action,
        "FAKE reject mapping",
        "FAKE bank source",
        decision="REJECT",
    )
    assert (await reviews.confirm(9001, action)).state == TopUpState.REJECTED
    assert (await topups.ingest(event)).state == TopUpState.REJECTED
    assert (await wallet.balance(101)).available == 0
