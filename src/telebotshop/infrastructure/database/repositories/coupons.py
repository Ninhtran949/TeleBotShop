from dataclasses import asdict
from uuid import uuid4, uuid5

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from telebotshop.application.ports.coupons import CouponRepository
from telebotshop.domain.models.coupons import Coupon, CouponDraft, CouponQuote, DiscountKind
from telebotshop.infrastructure.database.models.coupons import coupon_redemptions, coupons
from telebotshop.infrastructure.database.models.identity import audit_logs


def coupon_from(row):
    config = {key: row[key] for key in CouponDraft.__dataclass_fields__}
    config["kind"] = DiscountKind(config["kind"])
    return Coupon(row["id"], CouponDraft(**config), row["active"], row["created_by"])


class SqlCouponRepository(CouponRepository):
    def __init__(self, session):
        self.session = session

    async def _get(self, criterion, for_update):
        query = select(coupons).where(criterion)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else coupon_from(row)

    async def get(self, coupon_id, *, for_update=False):
        return await self._get(coupons.c.id == coupon_id, for_update)

    async def by_code(self, code, *, for_update=False):
        return await self._get(coupons.c.code == code, for_update)

    async def list(self, offset):
        rows = (
            await self.session.execute(
                select(coupons).order_by(coupons.c.code).offset(offset).limit(20)
            )
        ).mappings()
        return tuple(coupon_from(row) for row in rows)

    async def _audit(self, action_id, actor_id, target_id, action, changes):
        await self.session.execute(
            insert(audit_logs).values(
                id=uuid4(),
                actor_id=actor_id,
                target_id=target_id,
                correlation_id=uuid5(action_id, action),
                action=action,
                changes=changes,
            )
        )

    async def save(self, coupon):
        saved = await self.session.scalar(
            pg_insert(coupons)
            .values(
                id=coupon.id,
                **asdict(coupon.draft),
                active=coupon.active,
                created_by=coupon.created_by,
            )
            .on_conflict_do_nothing()
            .returning(coupons.c.id)
        )
        if saved is None:
            return False
        await self._audit(
            coupon.id, coupon.created_by, coupon.id, "coupon.created", {"code": coupon.draft.code}
        )
        return True

    async def change(self, action_id):
        row = (
            (
                await self.session.execute(
                    select(audit_logs).where(
                        audit_logs.c.correlation_id == uuid5(action_id, "coupon.active")
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return (
            None if row is None else (row["actor_id"], row["target_id"], row["changes"]["active"])
        )

    async def set_active(self, coupon, active, actor_id, action_id):
        await self.session.execute(
            update(coupons).where(coupons.c.id == coupon.id).values(active=active)
        )
        await self._audit(
            action_id,
            actor_id,
            coupon.id,
            "coupon.active",
            {"active": active, "code": coupon.draft.code},
        )

    async def uses(self, coupon_id, buyer_id):
        return tuple(
            (
                await self.session.execute(
                    select(
                        func.count(),
                        func.count().filter(coupon_redemptions.c.buyer_id == buyer_id),
                    ).where(coupon_redemptions.c.coupon_id == coupon_id)
                )
            ).one()
        )

    async def redemption(self, order_id):
        row = (
            (
                await self.session.execute(
                    select(coupon_redemptions).where(coupon_redemptions.c.order_id == order_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return (
            None
            if row is None
            else CouponQuote(**{key: row[key] for key in CouponQuote.__dataclass_fields__})
        )

    async def redeem(self, order, quote):
        snapshot = asdict(quote)
        await self.session.execute(
            insert(coupon_redemptions).values(
                order_id=order.id,
                buyer_id=order.buyer_id,
                **snapshot,
                total_credits=quote.total_credits,
            )
        )
        await self._audit(
            order.id,
            order.buyer_id,
            order.id,
            "coupon.redeemed",
            {
                **snapshot,
                "coupon_id": str(quote.coupon_id),
                "product_id": str(quote.product_id),
                "total_credits": quote.total_credits,
            },
        )
