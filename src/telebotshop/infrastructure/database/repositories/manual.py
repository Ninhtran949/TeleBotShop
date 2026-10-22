from dataclasses import asdict
from uuid import UUID

from sqlalchemy import RowMapping, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.manual import ManualRepository
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import PAGE_SIZE, Page, ProductType
from telebotshop.domain.models.fulfillment import DeliveryState
from telebotshop.domain.models.inventory import SealedPayload
from telebotshop.domain.models.manual import ManualAction, ManualWorkView
from telebotshop.infrastructure.database.models.identity import audit_logs
from telebotshop.infrastructure.database.models.manual import manual_actions
from telebotshop.infrastructure.database.models.orders import order_items, orders
from telebotshop.infrastructure.database.models.payments import fulfillments


def manual_action_from_row(row: RowMapping) -> ManualAction:
    return ManualAction(
        row["id"],
        row["actor_id"],
        row["work_id"],
        row["attempt_id"],
        row["expires_at"],
        ActionStatus(row["status"]),
    )


class SqlManualRepository(ManualRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def queue(self, page: int) -> Page[ManualWorkView]:
        rows = (
            await self.session.execute(
                select(
                    fulfillments.c.id,
                    fulfillments.c.order_id,
                    fulfillments.c.destination_telegram_id,
                    order_items.c.sku,
                    fulfillments.c.state,
                )
                .select_from(
                    fulfillments.join(
                        order_items, order_items.c.id == fulfillments.c.order_item_id
                    ).join(orders, orders.c.id == fulfillments.c.order_id)
                )
                .where(
                    fulfillments.c.product_type == ProductType.MANUAL_FULFILLMENT,
                    fulfillments.c.state == DeliveryState.AWAITING_MANUAL,
                    fulfillments.c.hold.is_(False),
                    orders.c.status == "FULFILLING",
                )
                .order_by(fulfillments.c.created_at, fulfillments.c.id)
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE + 1)
            )
        ).all()
        return Page(
            tuple(
                ManualWorkView(row[0], row[1], row[2], row[3], DeliveryState(row[4]))
                for row in rows[:PAGE_SIZE]
            ),
            page,
            len(rows) > PAGE_SIZE,
        )

    async def save(self, action: ManualAction, sealed: SealedPayload) -> None:
        await self.session.execute(
            insert(manual_actions).values(**asdict(action), **asdict(sealed))
        )

    async def action(self, action_id: UUID, *, for_update: bool = False) -> ManualAction | None:
        query = select(
            *(
                manual_actions.c[name]
                for name in (
                    "id",
                    "actor_id",
                    "work_id",
                    "attempt_id",
                    "expires_at",
                    "status",
                )
            )
        ).where(manual_actions.c.id == action_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else manual_action_from_row(row)

    async def evidence(self, action_id: UUID) -> SealedPayload:
        row = (
            await self.session.execute(
                select(
                    manual_actions.c.key_version,
                    manual_actions.c.nonce,
                    manual_actions.c.ciphertext,
                ).where(manual_actions.c.id == action_id)
            )
        ).one()
        return SealedPayload(*row)

    async def finish(self, action: ManualAction, *, cancelled: bool = False) -> None:
        changed = await self.session.execute(
            update(manual_actions)
            .where(
                manual_actions.c.id == action.id,
                manual_actions.c.status == ActionStatus.PENDING,
            )
            .values(
                status=ActionStatus.CANCELLED if cancelled else ActionStatus.APPLIED,
                completed_at=func.clock_timestamp(),
            )
        )
        if changed.rowcount != 1:
            raise InvalidAction("Manual confirmation changed")
        if not cancelled:
            changed = await self.session.execute(
                update(fulfillments)
                .where(
                    fulfillments.c.id == action.work_id,
                    fulfillments.c.state == DeliveryState.AWAITING_MANUAL,
                    fulfillments.c.active_attempt_id == action.attempt_id,
                    fulfillments.c.hold.is_(False),
                )
                .values(state=DeliveryState.SENT)
            )
            if changed.rowcount != 1:
                raise InvalidAction("Manual work changed")

    async def audit(
        self,
        actor_id: UUID,
        order_id: UUID,
        work_id: UUID,
        action: str,
        correlation_id: UUID,
        evidence_digest: str | None = None,
    ) -> None:
        changes = {"work_id": str(work_id)}
        if evidence_digest is not None:
            changes["evidence_digest"] = evidence_digest
        await self.session.execute(
            insert(audit_logs).values(
                actor_id=actor_id,
                action=action,
                target_id=order_id,
                correlation_id=correlation_id,
                changes=changes,
            )
        )
