from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.e2e import checkout, delivery
from tests.e2e.safety import E2EBlocked


def message(identifier, *, sender=42, media=None, text="", date=None, document=None):
    return SimpleNamespace(
        id=identifier, sender_id=sender, media=media, raw_text=text, date=date, document=document
    )


def test_invoice_uses_order_reference_instead_of_merchant_message_id():
    order = uuid4()
    expected = message(500, media=SimpleNamespace(start_param="order_" + order.hex))
    merchant_side_id = message(100, media=None)
    wrong_sender = message(501, sender=7, media=expected.media)
    assert (
        checkout.select_invoice([merchant_side_id, wrong_sender, expected], 42, order) is expected
    )


@pytest.mark.parametrize("count", [0, 2])
def test_invoice_requires_exactly_one_order_match(count):
    order = uuid4()
    matches = [
        message(i, media=SimpleNamespace(start_param="order_" + order.hex)) for i in range(count)
    ]
    with pytest.raises(E2EBlocked):
        checkout.select_invoice(matches, 42, order)


def test_delivery_uses_payload_attempt_time_and_sender_in_the_buyer_chat():
    sent = datetime(2026, 10, 23, tzinfo=UTC)
    expected = message(500, text="FAKE entitlement", date=sent)
    other = [
        message(1, text="FAKE entitlement", date=sent - timedelta(minutes=10)),
        message(2, text="FAKE entitlement", sender=7, date=sent),
        message(3, text="notification", date=sent),
    ]
    assert (
        delivery.select_delivery(other + [expected], 42, sent, sent, "FAKE entitlement") is expected
    )


def test_document_delivery_matches_only_a_document_during_the_send_attempt():
    sent = datetime(2026, 10, 23, tzinfo=UTC)
    expected = message(500, date=sent, document=object())
    old = message(1, date=sent - timedelta(minutes=10), document=object())
    assert (
        delivery.select_delivery([old, message(2, date=sent), expected], 42, sent, sent, None)
        is expected
    )


def test_duplicate_matching_deliveries_are_rejected():
    sent = datetime(2026, 10, 23, tzinfo=UTC)
    matches = [message(i, text="FAKE entitlement", date=sent) for i in (500, 501)]
    with pytest.raises(E2EBlocked):
        delivery.select_delivery(matches, 42, sent, sent, "FAKE entitlement")
