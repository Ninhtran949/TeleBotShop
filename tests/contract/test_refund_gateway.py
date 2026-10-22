from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import RefundStarPayment
from aiogram.types import StarTransactions

from telebotshop.domain.models.refunds import RefundRequest, RefundState
from telebotshop.infrastructure.telegram.refunds import TelegramRefundGateway


@pytest.mark.asyncio
async def test_refund_adapter_uses_only_verified_payer_and_charge_and_true_success():
    bot = AsyncMock()
    bot.refund_star_payment.return_value = True
    gateway = TelegramRefundGateway(bot)
    request = RefundRequest(uuid4(), "FAKE-charge", 101, datetime.now(UTC) + timedelta(seconds=60))
    assert (await gateway.refund(request)).state == RefundState.SUCCEEDED
    bot.refund_star_payment.assert_awaited_once_with(
        user_id=101, telegram_payment_charge_id="FAKE-charge", request_timeout=10.0
    )
    for result in [False, 1, "True", None]:
        bot.refund_star_payment.return_value = result
        assert (await gateway.refund(request)).state == RefundState.UNCERTAIN


@pytest.mark.asyncio
async def test_refund_timeout_and_already_refunded_are_uncertain_without_retry_or_raw_error(caplog):
    bot = AsyncMock()
    gateway = TelegramRefundGateway(bot)
    request = RefundRequest(uuid4(), "FAKE-charge", 101, datetime.now(UTC) + timedelta(seconds=60))
    method = RefundStarPayment(user_id=101, telegram_payment_charge_id="FAKE-charge")
    for error, state in [
        (TimeoutError("FAKE-secret"), RefundState.UNCERTAIN),
        (TelegramNetworkError(method, "FAKE-secret"), RefundState.UNCERTAIN),
        (TelegramBadRequest(method, "FAKE-secret ALREADY_REFUNDED"), RefundState.UNCERTAIN),
        (TelegramRetryAfter(method, "FAKE-secret", 2), RefundState.FAILED),
    ]:
        bot.refund_star_payment.reset_mock()
        bot.refund_star_payment.side_effect = error
        assert (await gateway.refund(request)).state == state
        bot.refund_star_payment.assert_awaited_once()
    assert "FAKE-secret" not in caplog.text


@pytest.mark.asyncio
async def test_transaction_reader_checks_direction_and_skips_fractional_or_other_sources():
    bot = AsyncMock()
    partner = {
        "type": "user",
        "user": {"id": 101, "is_bot": False, "first_name": "Fake"},
        "transaction_type": "invoice_payment",
        "invoice_payload": "FAKE-payload",
    }
    bot.get_star_transactions.return_value = StarTransactions.model_validate(
        {
            "transactions": [
                {"id": "FAKE-charge", "amount": 12, "date": 1700000000, "source": partner},
                {"id": "FAKE-charge", "amount": -12, "date": 1700000001, "receiver": partner},
                {
                    "id": "FAKE-fraction",
                    "amount": 1,
                    "nanostar_amount": 1,
                    "date": 1700000000,
                    "source": partner,
                },
                {"id": "FAKE-other", "amount": 12, "date": 1700000000, "source": {"type": "other"}},
            ]
        }
    )
    evidence = await TelegramRefundGateway(bot).transactions(100)
    assert len(evidence) == 2 and [e.refunded for e in evidence] == [False, True]
    assert all(
        e.payer_id == 101 and e.amount == 12 and e.payload == "FAKE-payload" for e in evidence
    )
    bot.get_star_transactions.assert_awaited_once_with(offset=100, limit=100, request_timeout=10)
