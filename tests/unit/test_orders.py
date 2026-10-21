from uuid import uuid4

import pytest

from telebotshop.application.services.orders import OrderService
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.orders import InvoiceDelivery, InvoiceStatus


def no_transaction():
    pytest.fail("Invalid checkout must be rejected before a database transaction")


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [True, 0, -1, 2**52, "101"])
async def test_checkout_rejects_invalid_identity_before_io(actor):
    with pytest.raises(InvalidInput):
        await OrderService(no_transaction).create(actor, uuid4(), uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["product", "action"])
@pytest.mark.parametrize("value", [None, "forged", 1])
async def test_checkout_requires_uuid_identifiers(field, value):
    with pytest.raises(InvalidInput):
        await OrderService(no_transaction).create(
            101, value if field == "product" else uuid4(), value if field == "action" else uuid4()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, 0, -1, 101, "10"])
async def test_maintenance_batch_validation_precedes_io(limit):
    with pytest.raises(InvalidInput):
        await OrderService(no_transaction).expire(limit)
    with pytest.raises(InvalidInput):
        await OrderService(no_transaction).recover_invoices(limit)


@pytest.mark.parametrize(
    ("status", "message_id"),
    [
        ("SENT", 1),
        (InvoiceStatus.READY, None),
        (InvoiceStatus.SENT, None),
        (InvoiceStatus.SENT, True),
        (InvoiceStatus.SENT, 0),
        (InvoiceStatus.SENT, -1),
        (InvoiceStatus.SENT, 1.5),
        (InvoiceStatus.REJECTED, 1),
        (InvoiceStatus.UNCERTAIN, 1),
    ],
)
def test_invalid_invoice_results_are_rejected(status, message_id):
    with pytest.raises(InvalidInput):
        InvoiceDelivery(status, message_id)
