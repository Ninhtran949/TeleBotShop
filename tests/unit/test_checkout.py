from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from telebotshop.application.services.checkout import CheckoutService
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.support import PublicTerms


@pytest.mark.asyncio
async def test_public_checkout_refuses_missing_or_changed_terms_before_accessing_database():
    def no_database():
        raise AssertionError("Unavailable terms must stop checkout before database access")

    unavailable = CheckoutService(no_database, AsyncMock(), None)
    assert unavailable.terms_version is None
    with pytest.raises(InvalidAction):
        await unavailable.prepare(101, uuid4(), uuid4())
    with pytest.raises(InvalidAction):
        await unavailable.pay(101, uuid4(), "0" * 24)

    service = CheckoutService(no_database, AsyncMock(), PublicTerms("FAKE approved terms"))
    assert len(service.terms_version) == 24
    with pytest.raises(InvalidAction):
        await service.pay(101, uuid4(), "0" * 24)
    changed = CheckoutService(no_database, AsyncMock(), PublicTerms("FAKE revised terms"))
    assert changed.terms_version != service.terms_version
