from dataclasses import asdict
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.resources import ResourceRepository
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.catalog import ProductType, metadata_from_record
from telebotshop.domain.models.inventory import SealedPayload
from telebotshop.domain.models.orders import Order
from telebotshop.domain.models.resources import ResourceState, ResourceVersion
from telebotshop.infrastructure.database.models.identity import audit_logs
from telebotshop.infrastructure.database.models.orders import order_items
from telebotshop.infrastructure.database.models.resources import order_resources, resource_versions


def resource_header():
    return select(
        *(
            resource_versions.c[name]
            for name in (
                "id",
                "product_id",
                "sku",
                "product_type",
                "metadata",
                "actor_id",
                "product_version",
                "expected_resource_id",
                "expires_at",
                "state",
            )
        )
    )


def resource_from_row(row: RowMapping) -> ResourceVersion:
    kind = ProductType(row["product_type"])
    return ResourceVersion(
        row["id"],
        row["product_id"],
        row["sku"],
        kind,
        metadata_from_record(kind, row["metadata"]),
        row["actor_id"],
        row["product_version"],
        row["expected_resource_id"],
        row["expires_at"],
        ResourceState(row["state"]),
    )


class SqlResourceRepository(ResourceRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current(self, product_id: UUID) -> ResourceVersion | None:
        row = (
            (
                await self.session.execute(
                    resource_header().where(
                        resource_versions.c.product_id == product_id,
                        resource_versions.c.state == ResourceState.ACTIVE,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else resource_from_row(row)

    async def version(
        self, resource_id: UUID, *, for_update: bool = False
    ) -> ResourceVersion | None:
        query = resource_header().where(resource_versions.c.id == resource_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else resource_from_row(row)

    async def save(self, version: ResourceVersion, sealed: SealedPayload) -> None:
        values = asdict(version)
        values["product_type"] = values.pop("kind")
        await self.session.execute(insert(resource_versions).values(**values, **asdict(sealed)))

    async def set_state(self, resource_id: UUID, state: ResourceState) -> None:
        result = await self.session.execute(
            update(resource_versions)
            .where(resource_versions.c.id == resource_id)
            .values(state=state)
        )
        if result.rowcount != 1:
            raise InvalidAction("Resource changed")

    async def bind(self, order: Order, resource_id: UUID) -> None:
        await self.session.execute(
            insert(order_resources).values(order_item_id=order.item_id, resource_id=resource_id)
        )

    async def for_order(self, order: Order) -> ResourceVersion | None:
        row = (
            (
                await self.session.execute(
                    resource_header()
                    .select_from(
                        resource_versions.join(
                            order_resources, order_resources.c.resource_id == resource_versions.c.id
                        ).join(order_items, order_items.c.id == order_resources.c.order_item_id)
                    )
                    .where(
                        order_items.c.id == order.item_id,
                        order_items.c.order_id == order.id,
                        resource_versions.c.product_id == order.product_id,
                        resource_versions.c.product_type == order.product_type,
                        resource_versions.c.state.in_(
                            [ResourceState.ACTIVE, ResourceState.RETIRED]
                        ),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else resource_from_row(row)

    async def audit(self, version: ResourceVersion, action: str) -> None:
        await self.session.execute(
            insert(audit_logs).values(
                actor_id=version.actor_id,
                action=action,
                target_id=version.product_id,
                correlation_id=uuid4(),
                changes={"resource_id": str(version.id)},
            )
        )
