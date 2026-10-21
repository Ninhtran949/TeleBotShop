from dataclasses import replace

import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.pre_checkout import PreCheckout


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query_id", ""),
        ("query_id", " "),
        ("query_id", "x" * 257),
        ("query_id", "fake\nquery"),
        ("query_id", "fake\x7fquery"),
        ("query_id", "\ud800"),
        ("buyer_telegram_id", True),
        ("buyer_telegram_id", 0),
        ("buyer_telegram_id", 2**52),
        ("currency", "USD"),
        ("currency", "xtr"),
        ("currency", None),
        ("amount", True),
        ("amount", 1.5),
        ("amount", 0),
        ("amount", 2**63),
        ("payload", None),
        ("payload", "x" * 31),
        ("payload", "x" * 33),
        ("payload", "'" * 32),
    ],
)
def test_pre_checkout_input_is_bounded_and_rejects_invalid_fields(field, value):
    request = PreCheckout("fake_query", 101, "XTR", 100, "x" * 32)
    with pytest.raises(InvalidInput):
        replace(request, **{field: value})
    assert "fake_query" not in repr(request) and "x" * 32 not in repr(request)
