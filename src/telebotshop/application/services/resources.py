from datetime import timedelta
from uuid import UUID, uuid4

from telebotshop.application.ports.inventory_crypto import InventoryCipher
from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.application.services.access import require_permission
from telebotshop.domain.errors import CatalogNotFound, InvalidAction, InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.catalog import Product, validate_code
from telebotshop.domain.models.resources import (
    RESOURCE_TYPES,
    ResourcePayload,
    ResourceState,
    ResourceVersion,
    validate_resource_payload,
)


class ResourceService:
    def __init__(self, uow: UnitOfWorkFactory, cipher: InventoryCipher) -> None:
        self.uow, self.cipher = uow, cipher

    async def product(self, actor_id: int, sku: str) -> Product:
        validate_code(sku)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            await require_permission(work, actor_id, Permission.MANAGE_INVENTORY)
            product = await work.catalog.product_by_sku(sku)
            if product is None or product.draft.product_type not in RESOURCE_TYPES:
                raise CatalogNotFound("Resource product unavailable")
            return product

    async def prepare(self, actor_id: int, sku: str, payload: ResourcePayload) -> ResourceVersion:
        validate_code(sku)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await require_permission(work, actor_id, Permission.MANAGE_INVENTORY)
            product = await work.catalog.product_by_sku(sku)
            if product is None or product.draft.product_type not in RESOURCE_TYPES:
                raise CatalogNotFound("Resource product unavailable")
            product = await work.catalog.product(product.id, for_update=True)
            assert product is not None
            validate_resource_payload(product.draft.product_type, product.draft.metadata, payload)
            await work.inventory.check_keys(self.cipher.proof())
            current = await work.resources.current(product.id)
            version = ResourceVersion(
                uuid4(),
                product.id,
                sku,
                product.draft.product_type,
                product.draft.metadata,
                actor.user_id,
                product.version,
                None if current is None else current.id,
                await work.orders.now() + timedelta(minutes=10),
            )
            await work.resources.save(version, self.cipher.seal(version.context, payload))
            await work.commit()
            return version

    async def confirm(self, actor_id: int, resource_id: UUID) -> bool:
        if not isinstance(resource_id, UUID):
            raise InvalidInput("Invalid resource action")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await require_permission(work, actor_id, Permission.MANAGE_INVENTORY)
            version = await work.resources.version(resource_id)
            if version is None or version.actor_id != actor.user_id:
                raise InvalidAction("Invalid resource action")
            product = await work.catalog.product(version.product_id, for_update=True)
            version = await work.resources.version(resource_id, for_update=True)
            if version.state in {ResourceState.ACTIVE, ResourceState.RETIRED}:
                return False
            current = await work.resources.current(version.product_id)
            if (
                product is None
                or product.version != version.product_version
                or version.state != ResourceState.PENDING
                or version.expires_at <= await work.orders.now()
                or (None if current is None else current.id) != version.expected_resource_id
            ):
                raise InvalidAction("Resource action expired or changed")
            await work.inventory.check_keys(self.cipher.proof())
            if current is not None:
                await work.resources.set_state(current.id, ResourceState.RETIRED)
            await work.resources.set_state(version.id, ResourceState.ACTIVE)
            await work.resources.audit(version, "resource.published")
            await work.commit()
            return True

    async def cancel(self, actor_id: int, resource_id: UUID) -> None:
        if not isinstance(resource_id, UUID):
            raise InvalidInput("Invalid resource action")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            actor = await require_permission(work, actor_id, Permission.MANAGE_INVENTORY)
            version = await work.resources.version(resource_id, for_update=True)
            if version is None or version.actor_id != actor.user_id:
                raise InvalidAction("Invalid resource action")
            if version.state in {ResourceState.ACTIVE, ResourceState.RETIRED}:
                raise InvalidAction("Published resource cannot be cancelled")
            if version.state == ResourceState.PENDING:
                await work.resources.set_state(resource_id, ResourceState.CANCELLED)
                await work.commit()
