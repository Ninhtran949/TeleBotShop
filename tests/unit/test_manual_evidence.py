from dataclasses import replace
from uuid import uuid4

import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.domain.models.manual import ManualEvidence, ManualEvidenceContext
from tests.inventory import fake_cipher


def test_manual_evidence_is_encrypted_and_bound_to_work_action_and_product():
    cipher = fake_cipher()
    context = ManualEvidenceContext(uuid4(), "DEMO-1", uuid4(), uuid4())
    payload = ManualEvidence("FAKE evidence for private delivery")
    sealed = cipher.seal(context, payload)
    assert cipher.open(context, sealed) == payload
    assert b"FAKE" not in sealed.ciphertext
    assert "FAKE" not in repr(payload) + repr(context)
    for changed in (
        replace(context, work_id=uuid4()),
        replace(context, action_id=uuid4()),
        replace(context, product_id=uuid4()),
    ):
        with pytest.raises(InventoryCryptoError):
            cipher.open(changed, sealed)


@pytest.mark.parametrize("evidence", ["", " ", "x" * 501, "FAKE\x00", "\ud800", None, 12])
def test_manual_completion_requires_bounded_nonempty_evidence(evidence):
    with pytest.raises(InvalidInput):
        ManualEvidence(evidence)
