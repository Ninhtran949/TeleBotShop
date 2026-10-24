from hashlib import sha256
from uuid import UUID

from telebotshop.application.ports.invoices import InvoiceSender
from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.application.services.invoices import InvoiceService
from telebotshop.application.services.orders import OrderService, owned_order
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.orders import InvoiceStatus, Order, OrderStatus
from telebotshop.domain.models.support import PublicTerms


class CheckoutService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        sender: InvoiceSender,
        terms: PublicTerms | None,
        *,
        enabled: bool = True,
    ) -> None:
        self.uow = uow
        self.enabled = enabled
        self.orders = OrderService(uow)
        self.invoices = InvoiceService(uow, sender)
        self.terms_sha256 = None if terms is None else sha256(terms.text.encode()).hexdigest()

    @property
    def terms_version(self) -> str | None:
        return None if self.terms_sha256 is None else self.terms_sha256[:24]

    async def prepare(self, actor_id: int, product_id: UUID, action_id: UUID) -> Order:
        if not self.enabled or self.terms_sha256 is None:
            raise InvalidAction("Checkout terms unavailable")
        return await self.orders.create(actor_id, product_id, action_id)

    async def pay(self, actor_id: int, order_id: UUID, terms_version: str) -> Order:
        if not self.enabled or self.terms_sha256 is None or terms_version != self.terms_version:
            raise InvalidAction("Checkout terms changed or unavailable")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            order = await owned_order(work, actor_id, order_id)
            if (
                order.status == OrderStatus.AWAITING_PAYMENT
                and order.invoice_status == InvoiceStatus.READY
                and order.expires_at > await work.orders.now()
            ):
                await work.orders.accept_terms(order, self.terms_sha256)
                await work.commit()
        return await self.invoices.send(actor_id, order_id)
