import hashlib
import json
from dataclasses import asdict, fields
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.fulfillment import FulfillmentRepository
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.catalog import ProductType, metadata_from_record
from telebotshop.domain.models.fulfillment import (
    DeliveryAttempt,
    DeliveryClaim,
    DeliveryCode,
    DeliveryResult,
    DeliveryState,
    EncryptedEntitlement,
    Fulfillment,
)
from telebotshop.domain.models.inventory import InventoryContext, SealedPayload
from telebotshop.domain.models.orders import OrderStatus
from telebotshop.domain.models.payments import CommittedWork
from telebotshop.domain.models.resources import ResourceContext, ResourceState
from telebotshop.infrastructure.database.models.fulfillment import (
    delivery_attempts,
    delivery_observations,
)
from telebotshop.infrastructure.database.models.identity import users
from telebotshop.infrastructure.database.models.inventory import inventory_units
from telebotshop.infrastructure.database.models.orders import order_items, orders
from telebotshop.infrastructure.database.models.payments import fulfillments
from telebotshop.infrastructure.database.models.resources import order_resources, resource_versions


def fulfillment_from_row(row: RowMapping) -> Fulfillment:
    committed = {field.name: row.get(field.name) for field in fields(CommittedWork)}
    committed["product_type"] = ProductType(committed["product_type"])
    return Fulfillment(
        CommittedWork(**committed),
        DeliveryState(row["state"]),
        row["active_attempt_id"],
        row["due_at"],
        row["send_count"],
        row["hold"],
    )


class SqlFulfillmentRepository(FulfillmentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claimable(self, now: datetime) -> Fulfillment | None:
        row = (
            (
                await self.session.execute(
                    select(fulfillments)
                    .where(
                        fulfillments.c.state.in_([DeliveryState.READY, DeliveryState.RETRY_WAIT]),
                        fulfillments.c.due_at <= now,
                        fulfillments.c.hold.is_(False),
                    )
                    .order_by(fulfillments.c.due_at, fulfillments.c.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else fulfillment_from_row(row)

    async def get(self, work_id: UUID, *, for_update: bool = False) -> Fulfillment | None:
        query = select(fulfillments).where(fulfillments.c.id == work_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else fulfillment_from_row(row)

    async def attempt(self, claim: DeliveryClaim) -> DeliveryAttempt | None:
        row = (
            (
                await self.session.execute(
                    select(delivery_attempts).where(
                        delivery_attempts.c.id == claim.attempt_id,
                        delivery_attempts.c.work_id == claim.work_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return (
            None
            if row is None
            else DeliveryAttempt(
                row["id"],
                row["work_id"],
                DeliveryState(row["state"]),
                row["claimed_at"],
                row["lease_expires_at"],
                row["sending_at"],
            )
        )

    async def start_claim(self, work: Fulfillment, attempt: DeliveryAttempt) -> None:
        await self.session.execute(insert(delivery_attempts).values(**asdict(attempt)))
        result = await self.session.execute(
            update(fulfillments)
            .where(
                fulfillments.c.id == work.work.id,
                fulfillments.c.state.in_([DeliveryState.READY, DeliveryState.RETRY_WAIT]),
                fulfillments.c.hold.is_(False),
            )
            .values(state=DeliveryState.CLAIMED, active_attempt_id=attempt.id)
        )
        if result.rowcount != 1:
            raise InvalidAction("Delivery claim changed")

    async def entitlement(self, work: Fulfillment) -> EncryptedEntitlement | None:
        committed = work.work
        if committed.resource_id is not None:
            return await self._resource_entitlement(work)
        row = (
            (
                await self.session.execute(
                    select(
                        inventory_units.c.id,
                        inventory_units.c.product_id,
                        inventory_units.c.product_type,
                        inventory_units.c.key_version,
                        inventory_units.c.nonce,
                        inventory_units.c.ciphertext,
                        order_items.c.sku,
                    )
                    .select_from(
                        inventory_units.join(
                            order_items,
                            (order_items.c.inventory_unit_id == inventory_units.c.id)
                            & (order_items.c.id == committed.order_item_id),
                        ).join(users, users.c.id == committed.buyer_id)
                    )
                    .where(
                        inventory_units.c.id == committed.inventory_unit_id,
                        inventory_units.c.status == "ALLOCATED",
                        inventory_units.c.allocated_item_id == committed.order_item_id,
                        inventory_units.c.product_type == committed.product_type,
                        users.c.telegram_id == committed.destination_telegram_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return (
            None
            if row is None
            else EncryptedEntitlement(
                InventoryContext(
                    row["product_id"], row["sku"], row["id"], ProductType(row["product_type"])
                ),
                SealedPayload(row["key_version"], row["nonce"], row["ciphertext"]),
            )
        )

    async def _resource_entitlement(self, work: Fulfillment) -> EncryptedEntitlement | None:
        committed = work.work
        row = (
            (
                await self.session.execute(
                    select(
                        resource_versions.c.id,
                        resource_versions.c.product_id,
                        resource_versions.c.sku,
                        resource_versions.c.product_type,
                        resource_versions.c.metadata,
                        resource_versions.c.key_version,
                        resource_versions.c.nonce,
                        resource_versions.c.ciphertext,
                    )
                    .select_from(
                        resource_versions.join(
                            order_resources, order_resources.c.resource_id == resource_versions.c.id
                        )
                        .join(order_items, order_items.c.id == order_resources.c.order_item_id)
                        .join(users, users.c.id == committed.buyer_id)
                    )
                    .where(
                        resource_versions.c.id == committed.resource_id,
                        resource_versions.c.product_type == committed.product_type,
                        resource_versions.c.product_id == order_items.c.product_id,
                        resource_versions.c.state.in_(
                            [ResourceState.ACTIVE, ResourceState.RETIRED]
                        ),
                        order_items.c.id == committed.order_item_id,
                        order_items.c.order_id == committed.order_id,
                        order_items.c.inventory_unit_id.is_(None),
                        users.c.telegram_id == committed.destination_telegram_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return (
            None
            if row is None
            else EncryptedEntitlement(
                ResourceContext(
                    row["product_id"], row["sku"], row["id"], ProductType(row["product_type"])
                ),
                SealedPayload(row["key_version"], row["nonce"], row["ciphertext"]),
                metadata_from_record(ProductType(row["product_type"]), row["metadata"]),
            )
        )

    async def start_send(self, claim: DeliveryClaim, now: datetime, deadline: datetime) -> None:
        result = await self.session.execute(
            update(delivery_attempts)
            .where(
                delivery_attempts.c.id == claim.attempt_id,
                delivery_attempts.c.work_id == claim.work_id,
                delivery_attempts.c.state == DeliveryState.CLAIMED,
                delivery_attempts.c.lease_expires_at > now,
            )
            .values(state=DeliveryState.SENDING, sending_at=now, lease_expires_at=deadline)
        )
        if result.rowcount != 1:
            raise InvalidAction("Delivery lease changed")
        await self.session.execute(
            update(fulfillments)
            .where(
                fulfillments.c.id == claim.work_id,
                fulfillments.c.active_attempt_id == claim.attempt_id,
                fulfillments.c.state == DeliveryState.CLAIMED,
            )
            .values(state=DeliveryState.SENDING, send_count=fulfillments.c.send_count + 1)
        )

    async def finish(
        self,
        claim: DeliveryClaim,
        result: DeliveryResult,
        state: DeliveryState,
        now: datetime,
        due_at: datetime,
        code: DeliveryCode | None,
    ) -> None:
        attempt_state = (
            DeliveryState.RETRYABLE
            if state == DeliveryState.RETRY_WAIT
            else DeliveryState.SENT
            if state == DeliveryState.AWAITING_MANUAL
            else state
        )
        changed = await self.session.execute(
            update(delivery_attempts)
            .where(
                delivery_attempts.c.id == claim.attempt_id,
                delivery_attempts.c.work_id == claim.work_id,
                delivery_attempts.c.state.in_([DeliveryState.CLAIMED, DeliveryState.SENDING]),
            )
            .values(
                state=attempt_state,
                completed_at=now,
                message_id=result.message_id if attempt_state == DeliveryState.SENT else None,
                error_code=code,
                retry_after=result.retry_after
                if attempt_state == DeliveryState.RETRYABLE
                else None,
            )
        )
        if changed.rowcount != 1:
            raise InvalidAction("Delivery attempt changed")
        await self.session.execute(
            update(fulfillments)
            .where(
                fulfillments.c.id == claim.work_id,
                fulfillments.c.active_attempt_id == claim.attempt_id,
            )
            .values(
                state=state,
                due_at=due_at,
                attention_code=code
                if state in {DeliveryState.FAILED, DeliveryState.UNCERTAIN}
                else None,
            )
        )

    async def abandon(self, claim: DeliveryClaim, now: datetime, code: DeliveryCode) -> None:
        await self.session.execute(
            update(delivery_attempts)
            .where(
                delivery_attempts.c.id == claim.attempt_id,
                delivery_attempts.c.work_id == claim.work_id,
                delivery_attempts.c.state == DeliveryState.CLAIMED,
            )
            .values(state=DeliveryState.ABANDONED, completed_at=now, error_code=code)
        )
        await self.session.execute(
            update(fulfillments)
            .where(
                fulfillments.c.id == claim.work_id,
                fulfillments.c.active_attempt_id == claim.attempt_id,
                fulfillments.c.state == DeliveryState.CLAIMED,
            )
            .values(state=DeliveryState.READY, active_attempt_id=None, due_at=now)
        )

    async def observe(self, claim: DeliveryClaim, result: DeliveryResult) -> None:
        values = asdict(result)
        values["error_code"] = values.pop("code")
        digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).digest()
        await self.session.execute(
            pg_insert(delivery_observations)
            .values(
                attempt_id=claim.attempt_id,
                digest=digest,
                **values,
            )
            .on_conflict_do_nothing()
        )

    async def expired(self, limit: int, now: datetime) -> tuple[DeliveryClaim, ...]:
        rows = (
            await self.session.execute(
                select(fulfillments.c.id, fulfillments.c.order_id, fulfillments.c.active_attempt_id)
                .select_from(
                    fulfillments.join(
                        delivery_attempts,
                        delivery_attempts.c.id == fulfillments.c.active_attempt_id,
                    ).join(orders, orders.c.id == fulfillments.c.order_id)
                )
                .where(
                    fulfillments.c.state.in_([DeliveryState.CLAIMED, DeliveryState.SENDING]),
                    delivery_attempts.c.lease_expires_at <= now,
                )
                .order_by(orders.c.id, fulfillments.c.id)
                .limit(limit)
                .with_for_update(of=orders, skip_locked=True)
            )
        ).all()
        return tuple(DeliveryClaim(*row) for row in rows)

    async def set_order_state(self, order_id: UUID, state: OrderStatus) -> None:
        await self.session.execute(
            update(orders).where(orders.c.id == order_id).values(status=state)
        )
