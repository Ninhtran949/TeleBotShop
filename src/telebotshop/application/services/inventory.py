from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from telebotshop.application.inventory_import import parse_inventory
from telebotshop.application.ports.inventory_crypto import InventoryCipher
from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.application.services.access import require_permission
from telebotshop.domain.errors import CatalogNotFound, InvalidAction, InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import Page, Product, validate_code, validate_page
from telebotshop.domain.models.inventory import (
    FINITE_TYPES,
    ImportFormat,
    InventoryAction,
    InventoryActionKind,
    InventoryContext,
    InventoryImportError,
    InventoryState,
    SealedUnit,
    StockPage,
    StockSummary,
)


class InventoryService:
    def __init__(self, uow: UnitOfWorkFactory, cipher: InventoryCipher, threshold: int) -> None:
        if type(threshold) is not int or not 0 <= threshold <= 1_000_000:
            raise InvalidInput("Invalid low-stock threshold")
        self.uow, self.cipher, self.threshold = uow, cipher, threshold

    async def check_keys(self) -> None:
        """Operator startup check; no payload is read or decrypted."""
        async with self.uow() as work:
            await work.inventory.check_keys(self.cipher.proof())
            await work.commit()

    async def prepare_import(
        self, actor_id: int, sku: str, content: bytes, format: ImportFormat
    ) -> InventoryAction:
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_INVENTORY)
            product = await self._product(work, sku)
            rows = parse_inventory(content, format, product.draft.product_type)
            await work.inventory.check_keys(self.cipher.proof())
            units = []
            seen = set()
            for row in rows:
                context = InventoryContext(product.id, sku, uuid4(), product.draft.product_type)
                fingerprint = self.cipher.fingerprint(context, row.payload)
                if fingerprint in seen:
                    raise InventoryImportError(row.row, "duplicate")
                seen.add(fingerprint)
                units.append(
                    SealedUnit(
                        context.unit_id,
                        row.row,
                        fingerprint,
                        self.cipher.seal(context, row.payload),
                    )
                )
            action = InventoryAction(
                uuid4(),
                actor.user_id,
                InventoryActionKind.IMPORT,
                product.id,
                None,
                len(units),
                datetime.now(UTC) + timedelta(minutes=10),
            )
            await work.inventory.save_action(action, tuple(units))
            await work.commit()
            return action

    async def prepare_retire(self, actor_id: int, sku: str, unit_id: UUID) -> InventoryAction:
        if not isinstance(unit_id, UUID):
            raise InvalidInput("Invalid inventory unit")
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_INVENTORY)
            product = await self._product(work, sku)
            unit = await work.inventory.unit(unit_id)
            if (
                unit is None
                or unit.product_id != product.id
                or unit.status != InventoryState.AVAILABLE
            ):
                raise InvalidAction("Unit cannot be retired")
            action = InventoryAction(
                uuid4(),
                actor.user_id,
                InventoryActionKind.RETIRE,
                product.id,
                unit_id,
                1,
                datetime.now(UTC) + timedelta(minutes=10),
            )
            await work.inventory.save_action(action, ())
            await work.commit()
            return action

    async def confirm(self, actor_id: int, action_id: UUID) -> bool:
        if not isinstance(action_id, UUID):
            raise InvalidInput("Invalid inventory action")
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_INVENTORY)
            await work.inventory.check_keys(self.cipher.proof())
            action = await work.inventory.action(action_id)
            if action is None or action.actor_id != actor.user_id:
                raise InvalidAction("Invalid inventory action")
            if action.status == ActionStatus.APPLIED:
                return False
            if action.status != ActionStatus.PENDING or action.expires_at <= datetime.now(UTC):
                raise InvalidAction("Invalid inventory action")
            product = await work.catalog.product(action.product_id)
            if product is None or product.draft.product_type not in FINITE_TYPES:
                raise InvalidAction("Product cannot hold units")
            if action.kind == InventoryActionKind.IMPORT:
                units = await work.inventory.pending_units(action.id)
                if len(units) != action.quantity:
                    raise InvalidAction("Invalid pending inventory")
                duplicate = await work.inventory.add_units(product, units)
                if duplicate is not None:
                    raise InventoryImportError(duplicate, "duplicate")
            elif action.unit_id is None or not await work.inventory.retire(
                product.id, action.unit_id
            ):
                raise InvalidAction("Unit cannot be retired")
            await work.inventory.audit(action)
            await work.inventory.complete(action)
            await work.commit()
            return True

    async def cancel(self, actor_id: int, action_id: UUID) -> None:
        if not isinstance(action_id, UUID):
            raise InvalidInput("Invalid inventory action")
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_INVENTORY)
            action = await work.inventory.action(action_id)
            if action is None or action.actor_id != actor.user_id:
                raise InvalidAction("Invalid inventory action")
            if action.status == ActionStatus.APPLIED:
                raise InvalidAction("Applied inventory cannot be cancelled")
            if action.status == ActionStatus.PENDING:
                await work.inventory.complete(action, cancelled=True)
                await work.commit()

    async def stock(self, actor_id: int, sku: str, page: int = 0) -> StockPage:
        validate_page(page)
        async with self.uow() as work:
            await require_permission(work, actor_id, Permission.MANAGE_INVENTORY)
            product = await self._product(work, sku)
            return await work.inventory.stock(product, page, self.threshold)

    async def low_stock(self, actor_id: int, page: int = 0) -> Page[StockSummary]:
        validate_page(page)
        async with self.uow() as work:
            await require_permission(work, actor_id, Permission.MANAGE_INVENTORY)
            return await work.inventory.low_stock(page, self.threshold)

    @staticmethod
    async def _product(work: UnitOfWork, sku: str) -> Product:
        validate_code(sku)
        product = await work.catalog.product_by_sku(sku)
        if product is None:
            raise CatalogNotFound("Product unavailable")
        if product.draft.product_type not in FINITE_TYPES:
            raise InvalidAction("Product cannot hold units")
        return product
