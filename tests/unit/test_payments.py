from dataclasses import replace

import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.payments import PaymentReceipt


@pytest.mark.parametrize(
    "field,value",
    [
        ("charge_id", ""),
        ("charge_id", "fake\ncharge"),
        ("charge_id", "x" * 1025),
        ("charge_id", "\ud800"),
        ("amount", True),
        ("amount", 100.0),
        ("amount", 2**63),
        ("amount", -(2**63) - 1),
        ("payer_id", 0),
        ("payer_id", True),
        ("currency", ""),
        ("currency", "x" * 17),
        ("payload", "é" * 65),
        ("provider_charge_id", "x" * 1025),
        ("is_recurring", 1),
        ("is_first_recurring", None),
        ("has_order_info", 1),
        ("subscription_expiration_date", -1),
        ("subscription_expiration_date", True),
        ("shipping_option_id", "\x00"),
    ],
)
def test_receipt_rejects_invalid_types_bounds_and_controls_without_echo(field, value):
    original = PaymentReceipt("FAKE-charge", 101, "XTR", 100, "x" * 32)
    with pytest.raises(InvalidInput) as raised:
        replace(original, **{field: value})
    assert str(raised.value) in {"Invalid payment receipt", "Invalid identity"}


def test_unknown_or_unsupported_financial_evidence_is_representable_without_pii():
    receipt = PaymentReceipt(
        "FAKE-charge",
        None,
        "USD",
        -1,
        "",
        is_recurring=True,
        subscription_expiration_date=0,
        has_order_info=True,
    )
    assert receipt.amount == -1 and receipt.payer_id is None
    assert receipt.digest() == replace(receipt).digest()
    assert receipt.digest() != replace(receipt, amount=0).digest()
    assert "FAKE-charge" not in repr(receipt)
    assert not {"name", "email", "address", "phone_number", "update"} & receipt.record().keys()
