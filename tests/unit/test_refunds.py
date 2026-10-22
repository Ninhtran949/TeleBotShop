from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.domain.models.refunds import (
    RefundEvidence,
    RefundEvidenceContext,
    RefundReceipt,
    RefundRequest,
    RefundResult,
    RefundState,
    StarEvidence,
)
from tests.inventory import fake_cipher


def test_refund_evidence_is_bounded_hidden_and_bound_to_payment_and_action():
    cipher = fake_cipher()
    context = RefundEvidenceContext(uuid4(), uuid4())
    evidence = RefundEvidence("FAKE-reviewed-policy-reference")
    sealed = cipher.seal(context, evidence)
    assert cipher.open(context, sealed) == evidence
    assert evidence.reference not in repr(evidence)
    for field in ["payment_id", "action_id"]:
        with pytest.raises(InventoryCryptoError):
            cipher.open(replace(context, **{field: uuid4()}), sealed)
    for reference in ["", "  ", "a" * 501, "bad\x00reference", "\ud800"]:
        with pytest.raises(InvalidInput):
            RefundEvidence(reference)
    with pytest.raises(InventoryCryptoError):
        cipher.seal(context, RefundReceipt("FAKE-charge", "XTR", 12, "FAKE-payload"))


def test_refund_boundary_requires_integer_stars_typed_result_and_utc_deadline():
    deadline = datetime.now(UTC) + timedelta(seconds=60)
    request = RefundRequest(uuid4(), "FAKE-charge", 101, deadline)
    assert "FAKE-charge" not in repr(request)
    for values in [{"payer_id": True}, {"charge_id": ""}, {"deadline": datetime.now()}]:
        with pytest.raises(InvalidInput):
            replace(request, **values)
    with pytest.raises(InvalidInput):
        RefundResult(RefundState.PENDING)
    with pytest.raises(InvalidInput):
        RefundResult("SUCCEEDED")
    assert RefundResult(RefundState.SUCCEEDED).state == RefundState.SUCCEEDED
    event = RefundReceipt("FAKE-charge", "XTR", 12, "FAKE-payload")
    assert len(event.digest()) == 32 and "FAKE-charge" not in repr(event)
    for amount in [True, 0, -1, 1.5]:
        with pytest.raises(InvalidInput):
            replace(event, amount=amount)
    with pytest.raises(InvalidInput):
        StarEvidence("FAKE-charge", 101, 12, None, True, datetime.now())
