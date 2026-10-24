from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from telebotshop.admin.payment_operator import authenticate_operator, run_operator
from telebotshop.domain.errors import AccessDenied
from telebotshop.domain.models.wallet import LedgerReconciliation


@pytest.mark.parametrize("expected,supplied", [(None, ""), ("", ""), ("x" * 32, "wrong")])
def test_operator_rejects_missing_or_wrong_credentials(expected, supplied):
    with pytest.raises(AccessDenied):
        authenticate_operator(expected, supplied)


@pytest.mark.asyncio
async def test_operator_authentication_precedes_application_effects(capsys):
    wallet = AsyncMock()
    wallet.reconcile.return_value = LedgerReconciliation(True, 0)
    services = SimpleNamespace(wallet=wallet)
    with pytest.raises(AccessDenied):
        await run_operator(["status"], 9001, "FAKE" * 8, services, read_token=lambda _: "wrong")
    wallet.reconcile.assert_not_awaited()
    assert (
        await run_operator(["status"], 9001, "FAKE" * 8, services, read_token=lambda _: "FAKE" * 8)
        == 0
    )
    wallet.reconcile.assert_awaited_once_with(9001)
    assert '"balanced": true' in capsys.readouterr().out
