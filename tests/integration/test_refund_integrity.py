from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.fulfillment import paid_work
from tests.refunds import refund_service


@pytest.mark.asyncio
async def test_verification_cannot_override_stored_charge_amount_or_known_payer(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO payment_verifications
                (payment_id,actor_id,payer_id,amount,transaction_at)
                SELECT p.id,u.id,999,p.amount+1,clock_timestamp() FROM payments p CROSS JOIN users u
                WHERE u.telegram_id=9001""")
            )


@pytest.mark.asyncio
async def test_pending_refund_cannot_have_a_send_window(migrated_database):
    engine = migrated_database
    await paid_work(engine)
    service = refund_service(engine)
    async with engine.connect() as connection:
        payment = await connection.scalar(text("SELECT id FROM payments"))
    action = await service.prepare(9001, payment, "FAKE-policy-reference")
    await service.cancel(9001, action.id)
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO refund_intents
                (id,payment_id,actor_id,expected,accept_uncertain,expires_at,status,
                    started_at,deadline,key_version,nonce,ciphertext)
                SELECT :id,payment_id,actor_id,expected,false,expires_at,
                    'PENDING',clock_timestamp(),clock_timestamp()+interval '60 seconds',
                    key_version,decode('000000000000000000000001','hex'),ciphertext
                FROM refund_intents WHERE id=:old"""),
                {"id": uuid4(), "old": action.id},
            )


@pytest.mark.asyncio
async def test_refund_identity_and_history_are_immutable_and_unproven_refund_is_rejected(
    migrated_database,
):
    engine = migrated_database
    await paid_work(engine)
    service = refund_service(engine)
    async with engine.connect() as connection:
        payment = await connection.scalar(text("SELECT id FROM payments"))
    await service.prepare(9001, payment, "FAKE-policy-reference")
    for statement in [
        "UPDATE refund_intents SET accept_uncertain=true",
        "UPDATE refund_intents SET ciphertext=decode('00','hex')",
        "UPDATE refund_intents SET "
        "status='SUCCEEDED',confirmed_by='API',completed_at=clock_timestamp(),"
        "started_at=clock_timestamp(),deadline=clock_timestamp()+interval '60 seconds'",
        "DELETE FROM refund_intents",
        "TRUNCATE refund_intents",
        "UPDATE orders SET status='REFUNDED'",
    ]:
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(text(statement))
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE refund_intents SET "
                    "status='SUCCEEDED',confirmed_by='API',completed_at=clock_timestamp(),"
                    "started_at=clock_timestamp(),deadline=clock_timestamp()+interval '60 seconds'"
                )
            )
            await connection.execute(
                text(
                    "UPDATE fulfillments SET hold=true,refund_intent_id=(SELECT id "
                    "FROM refund_intents)"
                )
            )
            await connection.execute(text("UPDATE orders SET status='REFUNDED'"))
