from hashlib import sha256
from uuid import UUID, uuid4

from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.application.services.coupons import quote_coupon
from telebotshop.application.services.orders import validate_uuid
from telebotshop.application.services.wallet import wallet_operator
from telebotshop.domain.errors import (
    AccessDenied,
    CatalogNotFound,
    InvalidAction,
    InvalidInput,
    OutOfStock,
)
from telebotshop.domain.models.catalog import SaleState
from telebotshop.domain.models.coupons import CouponQuote
from telebotshop.domain.models.identity import validate_telegram_id
from telebotshop.domain.models.inventory import FINITE_TYPES
from telebotshop.domain.models.orders import RESERVATION_TTL, Order, OrderStatus, PaymentMethod
from telebotshop.domain.models.payments import CommittedWork, validate_receipt_text
from telebotshop.domain.models.support import PublicTerms
from telebotshop.domain.models.wallet import JournalKind, WalletPermission, credits


class WalletCheckoutService:
    def __init__(self, uow: UnitOfWorkFactory, *, enabled: bool, terms: PublicTerms | None):
        self.uow, self.enabled = uow, enabled
        self.terms_version = None if terms is None else sha256(terms.text.encode()).hexdigest()

    def available(self):
        if self.enabled is not True or self.terms_version is None:
            raise InvalidAction("Credit checkout unavailable")

    async def price(self, product_id: UUID) -> int | None:
        self.available()
        validate_uuid(product_id)
        async with self.uow() as work:
            return await work.credit_orders.price(product_id)

    async def set_price(
        self, actor_id: int, product_id: UUID, amount: int, action_id: UUID, reason: str
    ) -> None:
        self.available()
        validate_uuid(product_id)
        validate_uuid(action_id)
        credits(amount)
        validate_receipt_text(reason, 300)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.PRICE)
            if await work.catalog.product(product_id, for_update=True) is None:
                raise CatalogNotFound("Product unavailable")
            await work.credit_orders.set_price(product_id, amount, actor.user_id, action_id, reason)
            await work.commit()

    async def buy(
        self,
        actor_id: int,
        product_id: UUID,
        action_id: UUID,
        expected_price: int,
        terms_version: str,
        *,
        coupon: CouponQuote | None = None,
    ) -> Order:
        self.available()
        validate_telegram_id(actor_id)
        validate_uuid(product_id)
        validate_uuid(action_id)
        credits(expected_price)
        if coupon is not None and not isinstance(coupon, CouponQuote):
            raise InvalidInput("Invalid coupon snapshot")
        if terms_version != self.terms_version:
            raise InvalidAction("Credit checkout terms changed")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            buyer_id = await work.orders.lock_buyer(actor_id)
            if buyer_id is None:
                raise AccessDenied("Customer unavailable")
            existing = await work.orders.by_action(buyer_id, action_id)
            if existing is not None:
                if (
                    existing.product_id != product_id
                    or existing.payment_method != PaymentMethod.WALLET_CREDIT
                    or existing.price_credits != expected_price
                    or await work.coupons.redemption(existing.id) != coupon
                ):
                    raise InvalidAction("Checkout action conflicts")
                return existing
            product = await work.catalog.product(product_id, for_update=True)
            if product is None or product.sale_state != SaleState.ACTIVE:
                raise CatalogNotFound("Product unavailable")
            price = await work.credit_orders.price(product_id)
            if price is None:
                raise InvalidAction("Credit price changed or unavailable")
            if coupon is not None:
                current = await work.coupons.get(coupon.coupon_id, for_update=True)
                if current is None:
                    raise InvalidAction("Coupon unavailable")
                fresh = await quote_coupon(work, current, buyer_id, product, price)
                if fresh != coupon:
                    raise InvalidAction("Coupon quote changed")
                price = fresh.total_credits
            if price != expected_price:
                raise InvalidAction("Credit price changed or unavailable")
            unit_id, resource = None, None
            if product.draft.product_type in FINITE_TYPES:
                unit_id = await work.orders.available_unit(product_id)
                if unit_id is None:
                    raise OutOfStock("Product out of stock")
            else:
                resource = await work.resources.current(product_id)
                if resource is None or resource.metadata != product.draft.metadata:
                    raise CatalogNotFound("Product source unavailable")
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
                None,
                1,
                now,
                now + RESERVATION_TTL,
                OrderStatus.AWAITING_PAYMENT,
                None,
                None,
                payment_method=PaymentMethod.WALLET_CREDIT,
                price_credits=price,
            )
            await work.orders.save(order, action_id)
            if coupon is not None:
                await work.coupons.redeem(order, coupon)
            if resource is not None:
                await work.resources.bind(order, resource.id)
            entry = await work.wallet.post(
                f"purchase:{order.id}",
                JournalKind.PURCHASE,
                order.id,
                buyer_id,
                -price,
                buyer_id,
                "Credit checkout",
                bytes.fromhex(self.terms_version),
            )
            payment_id = await work.credit_orders.pay(order, entry.id, actor_id)
            await work.payments.mark_paid(order, None)
            if unit_id is not None:
                await work.payments.allocate(order)
            await work.payments.save_work(
                CommittedWork(
                    uuid4(),
                    order.id,
                    order.item_id,
                    payment_id,
                    unit_id,
                    buyer_id,
                    actor_id,
                    order.product_type,
                    None if resource is None else resource.id,
                )
            )
            await work.wallet.audit(buyer_id, order.id, "wallet.purchase", entry.id)
            await work.orders.audit(order, "order.created")
            result = await work.orders.order(order.id)
            await work.commit()
            return result
