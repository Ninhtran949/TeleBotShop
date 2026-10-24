from uuid import UUID, uuid4

from telebotshop.application.ports.invoices import InvoiceSender
from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.application.services.orders import close_unpaid, owned_order
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.inventory import FINITE_TYPES
from telebotshop.domain.models.orders import (
    CloseReason,
    InvoiceDelivery,
    InvoiceRequest,
    InvoiceStatus,
    Order,
    OrderStatus,
)


class InvoiceService:
    def __init__(self, uow: UnitOfWorkFactory, sender: InvoiceSender) -> None:
        self.uow, self.sender = uow, sender

    async def send(self, actor_id: int, order_id: UUID) -> Order:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            order = await owned_order(work, actor_id, order_id)
            if order.status != OrderStatus.AWAITING_PAYMENT:
                return order
            if order.expires_at <= await work.orders.now():
                await close_unpaid(work, order, CloseReason.EXPIRED)
                result = await work.orders.order(order.id)
                assert result is not None
                await work.commit()
                return result
            if order.invoice_status != InvoiceStatus.READY:
                return order
            available = (
                await work.orders.has_reservation(order)
                if order.product_type in FINITE_TYPES
                else await work.resources.for_order(order) is not None
            )
            if not available:
                raise InvalidAction("Reservation unavailable")
            attempt_id = uuid4()
            await work.orders.claim_invoice(order, attempt_id)
            await work.orders.audit(order, "invoice.sending")
            claimed = await work.orders.order(order.id)
            assert claimed is not None
            request = InvoiceRequest(claimed, actor_id, attempt_id)
            await work.commit()

        # A committed attempt survives interruption or a lost response; never auto-resend it.
        delivery = await self.sender.send(request)

        async with self.uow() as work:
            await work.orders.configure_timeouts()
            order = await work.orders.order(order_id, for_update=True)
            if order is None or order.attempt_id != attempt_id:
                raise InvalidAction("Invoice attempt unavailable")
            if order.invoice_status not in {InvoiceStatus.SENDING, InvoiceStatus.UNCERTAIN}:
                return order
            if order.invoice_status == delivery.status == InvoiceStatus.UNCERTAIN:
                return order
            if (
                delivery.status == InvoiceStatus.REJECTED
                and await work.orders.approved_query(order.id) is not None
            ):
                delivery = InvoiceDelivery(InvoiceStatus.UNCERTAIN)
            await work.orders.finish_invoice(order, delivery)
            await work.orders.audit(order, f"invoice.{delivery.status.value.lower()}")
            if (
                delivery.status == InvoiceStatus.REJECTED
                and order.status == OrderStatus.AWAITING_PAYMENT
            ):
                await close_unpaid(work, order, CloseReason.REJECTED)
            result = await work.orders.order(order.id)
            assert result is not None
            await work.commit()
            return result
