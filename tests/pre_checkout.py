from dataclasses import replace
from uuid import uuid4

from telebotshop.domain.models.orders import InvoiceDelivery, InvoiceStatus
from telebotshop.domain.models.pre_checkout import PreCheckout
from telebotshop.infrastructure.database.uow import unit_of_work


def checkout_request(order, *, query_id="fake_query", buyer=101, amount=None):
    return PreCheckout(
        query_id, buyer, "XTR", order.price_stars if amount is None else amount, order.payload
    )


async def payable_invoice(engine, order, status=InvoiceStatus.SENT):
    async with unit_of_work(engine) as work:
        attempt_id = uuid4()
        await work.orders.claim_invoice(order, attempt_id)
        claimed = replace(order, attempt_id=attempt_id, invoice_status=InvoiceStatus.SENDING)
        if status != InvoiceStatus.SENDING:
            await work.orders.finish_invoice(
                claimed, InvoiceDelivery(status, 20 if status == InvoiceStatus.SENT else None)
            )
        await work.commit()
