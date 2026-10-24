import asyncio

from telebotshop.application.ports.pre_checkout import PRE_CHECKOUT_DB_BUDGET
from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.catalog import SaleState
from telebotshop.domain.models.inventory import FINITE_TYPES
from telebotshop.domain.models.orders import RESERVATION_TTL, InvoiceStatus, OrderStatus
from telebotshop.domain.models.pre_checkout import PreCheckout


class PreCheckoutService:
    def __init__(self, uow: UnitOfWorkFactory, *, enabled: bool = True) -> None:
        self.uow = uow
        self.enabled = enabled

    async def check(self, request: PreCheckout) -> bool:
        if not self.enabled:
            return False
        async with asyncio.timeout(PRE_CHECKOUT_DB_BUDGET), self.uow() as work:
            await work.orders.configure_timeouts()
            profile = await work.identity.get_profile(request.buyer_telegram_id)
            if profile is None:
                return False
            order = await work.orders.by_payload(request.payload)
            if (
                order is None
                or order.buyer_id != profile.id
                or request.currency != "XTR"
                or order.price_stars != request.amount
                or order.status != OrderStatus.AWAITING_PAYMENT
                or order.invoice_status
                not in {
                    InvoiceStatus.SENDING,
                    InvoiceStatus.SENT,
                    InvoiceStatus.UNCERTAIN,
                }
            ):
                return False
            product = await work.catalog.product(order.product_id, for_update=True)
            if product is None or product.sale_state != SaleState.ACTIVE:
                return False
            available = (
                await work.orders.has_reservation(order, for_update=True)
                if order.product_type in FINITE_TYPES
                else await work.resources.for_order(order) is not None
            )
            if not available:
                return False
            now = await work.orders.now()
            if order.expires_at <= now:
                return False
            approved_query = await work.orders.approved_query(order.id)
            if approved_query is not None:
                return approved_query == request.query_id
            if not await work.orders.save_approval(order.id, request.query_id, now):
                return False
            if not await work.orders.extend_reservation(
                order, max(order.expires_at, now + RESERVATION_TTL)
            ):
                raise InvalidAction("Reservation unavailable")
            await work.orders.audit(order, "pre_checkout.approved")
            await work.commit()
            return True
