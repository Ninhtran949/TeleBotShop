from uuid import UUID

from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.application.services.orders import validate_uuid
from telebotshop.application.services.wallet import wallet_operator
from telebotshop.domain.errors import AccessDenied, InvalidAction, InvalidInput
from telebotshop.domain.models.catalog import Product, SaleState, validate_code
from telebotshop.domain.models.coupons import Coupon, CouponDraft, CouponQuote, coupon_code
from telebotshop.domain.models.identity import validate_telegram_id
from telebotshop.domain.models.wallet import WalletPermission


async def quote_coupon(
    work: UnitOfWork, coupon: Coupon, buyer_id: UUID, product: Product, base_credits: int
) -> CouponQuote:
    total_used, user_used = await work.coupons.uses(coupon.id, buyer_id)
    return coupon.quote(
        product.id,
        product.draft.category_code,
        base_credits,
        await work.orders.now(),
        total_used,
        user_used,
    )


class CouponService:
    def __init__(self, uow: UnitOfWorkFactory):
        self.uow = uow

    async def get(self, actor_id: int, code: str) -> Coupon:
        code = coupon_code(code)
        async with self.uow() as work:
            await wallet_operator(work, actor_id, WalletPermission.PRICE)
            coupon = await work.coupons.by_code(code)
            if coupon is None:
                raise InvalidAction("Coupon unavailable")
            return coupon

    async def list(self, actor_id: int, offset: int = 0) -> tuple[Coupon, ...]:
        if type(offset) is not int or not 0 <= offset <= 10000:
            raise InvalidInput("Invalid coupon page")
        async with self.uow() as work:
            await wallet_operator(work, actor_id, WalletPermission.PRICE)
            return await work.coupons.list(offset)

    async def create(self, actor_id: int, draft: CouponDraft, action_id: UUID) -> Coupon:
        validate_uuid(action_id)
        if not isinstance(draft, CouponDraft):
            raise InvalidInput("Invalid coupon configuration")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.PRICE)
            existing = await work.coupons.get(action_id)
            if existing is not None:
                if existing.draft != draft or existing.created_by != actor.user_id:
                    raise InvalidAction("Coupon action conflicts")
                return existing
            if (
                draft.expires_at <= await work.orders.now()
                or draft.product_id is not None
                and await work.catalog.product(draft.product_id) is None
                or draft.category_code is not None
                and await work.catalog.category(draft.category_code) is None
            ):
                raise InvalidAction("Coupon scope or expiry unavailable")
            result = Coupon(action_id, draft, True, actor.user_id)
            if not await work.coupons.save(result):
                existing = await work.coupons.get(action_id)
                if (
                    existing is None
                    or existing.draft != draft
                    or existing.created_by != actor.user_id
                ):
                    raise InvalidAction("Coupon already exists or action conflicts")
                return existing
            await work.commit()
            return result

    async def set_active(
        self, actor_id: int, code: str, active: bool, action_id: UUID, *, expected_active: bool
    ) -> None:
        code = coupon_code(code)
        validate_uuid(action_id)
        if type(active) is not bool or type(expected_active) is not bool:
            raise InvalidInput("Invalid coupon state")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await wallet_operator(work, actor_id, WalletPermission.PRICE)
            coupon = await work.coupons.by_code(code, for_update=True)
            if coupon is None:
                raise InvalidAction("Coupon unavailable")
            previous = await work.coupons.change(action_id)
            if previous is not None:
                if previous != (actor.user_id, coupon.id, active):
                    raise InvalidAction("Coupon action conflicts")
                return
            if coupon.active != expected_active:
                raise InvalidAction("Coupon state changed")
            await work.coupons.set_active(coupon, active, actor.user_id, action_id)
            await work.commit()

    async def preview(self, actor_id: int, sku: str, code: str) -> CouponQuote:
        validate_telegram_id(actor_id)
        validate_code(sku)
        code = coupon_code(code)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            buyer = await work.identity.get_profile(actor_id)
            if buyer is None:
                raise AccessDenied("Customer unavailable")
            product = await work.catalog.product_by_sku(sku)
            coupon = await work.coupons.by_code(code)
            if product is None or product.sale_state != SaleState.ACTIVE or coupon is None:
                raise InvalidAction("Coupon or product unavailable")
            price = await work.credit_orders.price(product.id)
            if price is None:
                raise InvalidAction("Credit price unavailable")
            return await quote_coupon(work, coupon, buyer.id, product, price)
