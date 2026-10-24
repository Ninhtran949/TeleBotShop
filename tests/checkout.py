from functools import partial
from unittest.mock import AsyncMock

from telebotshop.application.ports.invoices import InvoiceSender
from telebotshop.application.services.checkout import CheckoutService
from telebotshop.domain.models.support import PublicTerms
from telebotshop.infrastructure.database.uow import unit_of_work


def checkout_service(engine) -> CheckoutService:
    return CheckoutService(
        partial(unit_of_work, engine),
        AsyncMock(spec=InvoiceSender),
        PublicTerms("FAKE public terms for isolated bot tests"),
    )
