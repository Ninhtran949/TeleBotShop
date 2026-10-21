import secrets
from uuid import UUID, uuid4

from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.domain.errors import (
    AccessDenied,
    CatalogNotFound,
    InvalidAction,
    InvalidInput,
    OutOfStock,
)
from telebotshop.domain.models.catalog import SaleState
from telebotshop.domain.models.identity import validate_telegram_id
from telebotshop.domain.models.inventory import FINITE_TYPES
from telebotshop.domain.models.orders import (
    RESERVATION_TTL,
    CloseReason,
    InvoiceDelivery,
    InvoiceStatus,
    Order,
    OrderStatus,
)


def validate_uuid(value: UUID) -> None:
    if not isinstance(value, UUID):
        raise InvalidInput("Invalid order identifier")


def validate_batch(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidInput("Invalid maintenance batch")


async def owned_order(work: UnitOfWork, actor_id: int, order_id: UUID) -> Order:
    validate_telegram_id(actor_id)
    validate_uuid(order_id)
    profile = await work.identity.get_profile(actor_id)
    order = await work.orders.order(order_id, for_update=True)
    if profile is None or order is None or order.buyer_id != profile.id:
        raise AccessDenied("Order unavailable")
    return order


async def close_unpaid(work: UnitOfWork, order: Order, reason: CloseReason) -> None:
    if order.status != OrderStatus.AWAITING_PAYMENT:
        raise InvalidAction("Order cannot be closed")
    await work.orders.close(order, reason)
    if not await work.orders.release(order):
        raise InvalidAction("Reservation unavailable")
    await work.orders.audit(order, f"order.{reason.value.lower()}")


class OrderService:
    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self.uow = uow

    async def create(self, actor_id: int, product_id: UUID, action_id: UUID) -> Order:
        validate_telegram_id(actor_id)
        validate_uuid(product_id)
        validate_uuid(action_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            buyer_id = await work.orders.lock_buyer(actor_id)
            if buyer_id is None:
                raise AccessDenied("Customer unavailable")
            existing = await work.orders.by_action(buyer_id, action_id)
            if existing is not None:
                if existing.product_id != product_id:
                    raise InvalidAction("Checkout action belongs to another product")
                return existing
            product = await work.catalog.product(product_id, for_update=True)
            if (
                product is None
                or product.sale_state != SaleState.ACTIVE
                or product.draft.product_type not in FINITE_TYPES
            ):
                raise CatalogNotFound("Product unavailable for checkout")
            unit_id = await work.orders.available_unit(product_id)
            if unit_id is None:
                raise OutOfStock("Product out of stock")
            now = await work.orders.now()
            order = Order(
                uuid4(),
                buyer_id,
                uuid4(),
                product_id,
                unit_id,
                product.draft.sku,
                product.draft.product_type,
                product.draft.name,
                product.draft.description,
                product.draft.price_stars,
                1,
                now,
                now + RESERVATION_TTL,
                OrderStatus.AWAITING_PAYMENT,
                InvoiceStatus.READY,
                secrets.token_urlsafe(24),
            )
            await work.orders.save(order, action_id)
            await work.orders.audit(order, "order.created")
            await work.commit()
            return order

    async def get(self, actor_id: int, order_id: UUID) -> Order:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            return await owned_order(work, actor_id, order_id)

    async def cancel(self, actor_id: int, order_id: UUID) -> Order:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            order = await owned_order(work, actor_id, order_id)
            if order.status == OrderStatus.EXPIRED:
                return order
            await close_unpaid(work, order, CloseReason.CANCELLED)
            result = await work.orders.order(order.id)
            assert result is not None
            await work.commit()
            return result

    async def expire(self, limit: int = 100) -> int:
        validate_batch(limit)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            orders = await work.orders.due_orders(limit)
            now = await work.orders.now()
            count = 0
            for order in orders:
                if order.status == OrderStatus.AWAITING_PAYMENT and order.expires_at <= now:
                    await close_unpaid(work, order, CloseReason.EXPIRED)
                    count += 1
            await work.commit()
            return count

    async def recover_invoices(self, limit: int = 100) -> int:
        validate_batch(limit)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            orders = await work.orders.stale_invoices(limit)
            for order in orders:
                await work.orders.finish_invoice(order, InvoiceDelivery(InvoiceStatus.UNCERTAIN))
                await work.orders.audit(order, "invoice.uncertain")
            await work.commit()
            return len(orders)
