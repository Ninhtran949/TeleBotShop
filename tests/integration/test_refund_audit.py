from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from telebotshop.domain.models.refunds import RefundResult, RefundState, StarEvidence
from tests.fulfillment import fulfillment_service, paid_work
from tests.payments import receipt
from tests.refunds import refund_service, refund_staff


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_failure", [False, True])
async def test_reconciliation_audits_actual_staff_and_audit_failure_rolls_back_every_change(
    migrated_database, audit_failure
):
    engine = migrated_database
    order, _ = await paid_work(engine)
    await refund_staff(engine)
    service = refund_service(engine)
    service.gateway.refund.return_value = RefundResult(RefundState.UNCERTAIN)
    async with engine.connect() as connection:
        payment = await connection.scalar(text("SELECT id FROM payments"))
    action = await service.prepare(9001, payment, "FAKE-reviewed")
    await service.confirm(9001, action.id)
    service.gateway.transactions.return_value = (
        StarEvidence(
            receipt(order).charge_id, 101, order.price_stars, None, True, datetime.now(UTC)
        ),
    )
    if audit_failure:
        async with engine.begin() as connection:
            await connection.execute(
                text("""CREATE FUNCTION reject_reconcile_audit() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.action='refund.reconciled' THEN RAISE EXCEPTION 'FAKE audit failure'; END IF;
                RETURN NEW; END $$""")
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER reject_reconcile BEFORE INSERT ON audit_logs FOR "
                    "EACH ROW EXECUTE FUNCTION reject_reconcile_audit()"
                )
            )
        with pytest.raises(DBAPIError):
            await service.reconcile(202, payment)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT status FROM orders")) == "PAID"
            assert await connection.scalar(text("SELECT status FROM refund_intents")) == "UNCERTAIN"
            assert await connection.scalar(text("SELECT count(*) FROM refund_receipts")) == 0
    else:
        assert await service.reconcile(202, payment)
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT u.telegram_id,a.target_id,a.changes FROM audit_logs a "
                        "JOIN users u ON u.id=a.actor_id WHERE a.action='refund.reconciled'"
                    )
                )
            ).one()
            assert row.telegram_id == 202 and row.target_id == payment
            assert row.changes == {"refund_id": str(action.id)}


@pytest.mark.asyncio
async def test_refunded_work_still_requires_matching_active_attempt_state(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    worker = fulfillment_service(engine, AsyncMock())
    claim = await worker.claim()
    service = refund_service(engine)
    async with engine.connect() as connection:
        payment = await connection.scalar(text("SELECT id FROM payments"))
    action = await service.prepare(9001, payment, "FAKE-reviewed")
    await service.confirm(9001, action.id)
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE delivery_attempts SET "
                    "state='ABANDONED',completed_at=clock_timestamp(),"
                    "error_code='HELD' WHERE id=:id"
                ),
                {"id": claim.attempt_id},
            )
