from dataclasses import replace

import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.fulfillment import (
    DeliveryCode,
    DeliveryPolicy,
    DeliveryResult,
    DeliveryState,
)
from tests.contract.test_delivery import request


@pytest.mark.parametrize(
    "changes",
    [
        {"claim_seconds": 0},
        {"claim_seconds": True},
        {"max_attempts": 0},
        {"max_attempts": 6},
        {"timeout_seconds": 21},
        {"send_seconds": 11},
        {"retry_base_seconds": 61},
        {"retry_cap_seconds": 3601},
    ],
)
def test_delivery_policy_bounds(changes):
    with pytest.raises(InvalidInput):
        DeliveryPolicy(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"destination_telegram_id": 0},
        {"deadline": None},
        {"context": None},
        {"timeout_seconds": True},
        {"timeout_seconds": 21},
        {"payload": "FAKE-private"},
    ],
)
def test_delivery_request_rejects_malformed_payload_or_destination_without_echo(changes):
    with pytest.raises(InvalidInput) as raised:
        replace(request(), **changes)
    assert "FAKE-private" not in str(raised.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "SENT"},
        {"message_id": True},
        {"message_id": 0},
        {"code": DeliveryCode.NETWORK},
        {"retry_after": 1},
    ],
)
def test_sent_result_requires_positive_message_and_no_error(changes):
    with pytest.raises(InvalidInput):
        replace(DeliveryResult(DeliveryState.SENT, message_id=1), **changes)


@pytest.mark.parametrize("retry_after", [None, True, 0, -1, 86401, 1.5])
def test_rate_limit_result_has_bounded_integer_retry_after(retry_after):
    with pytest.raises(InvalidInput):
        DeliveryResult(
            DeliveryState.RETRYABLE, code=DeliveryCode.RATE_LIMIT, retry_after=retry_after
        )
