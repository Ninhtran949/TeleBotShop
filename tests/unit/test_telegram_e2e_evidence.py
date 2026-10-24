import json
from uuid import uuid4

import pytest

from tests.e2e.evidence import Evidence
from tests.e2e.safety import E2EBlocked


def test_evidence_accepts_only_bounded_case_names_and_internal_order_ids(tmp_path):
    evidence = Evidence(tmp_path, "owner")
    order = uuid4()
    evidence.passed("checkout.key", order_id=order)
    result = json.loads(next((tmp_path / ".cache/t14-e2e/evidence").glob("*.json")).read_text())
    assert result["order_id"] == str(order)
    assert result["status"] == "PASS"
    assert set(result) == {"case", "actor", "status", "at", "source_sha256", "order_id"}
    with pytest.raises(E2EBlocked):
        evidence.passed("../../token")
    with pytest.raises(E2EBlocked):
        evidence.passed("checkout.key", order_id="FAKE-CHARGE-SECRET")
