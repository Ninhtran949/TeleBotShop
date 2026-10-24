from decimal import Decimal

import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.wallet import credits, quote_credits


@pytest.mark.parametrize("value", [True, False, 1.0, Decimal("1"), "1", 0, -1, 10**15])
def test_credit_amounts_reject_non_integer_nonpositive_and_overflow(value):
    with pytest.raises(InvalidInput):
        credits(value)


def test_usdt_quote_uses_decimal_and_explicit_round_down():
    assert quote_credits(Decimal("1.123456"), Decimal("25000")) == 28086


@pytest.mark.parametrize(
    "amount,rate",
    [
        (Decimal("NaN"), Decimal("1")),
        (Decimal("1"), Decimal("Infinity")),
        (Decimal("-1"), Decimal("25000")),
        (1.0, Decimal("25000")),
        (Decimal("1"), Decimal("0")),
        (Decimal("0.000000001"), Decimal("0.1")),
    ],
)
def test_quote_rejects_invalid_unbounded_or_subcredit_values(amount, rate):
    with pytest.raises(InvalidInput):
        quote_credits(amount, rate)
