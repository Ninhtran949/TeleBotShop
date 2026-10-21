from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.application.services.access import require_permission
from telebotshop.domain.errors import CatalogNotFound, InvalidAction, InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import (
    Category,
    CategoryDraft,
    Page,
    Product,
    ProductDraft,
    SaleState,
    validate_code,
    validate_page,
    validate_search,
)
from telebotshop.domain.models.catalog_action import (
    CatalogAction,
    CatalogActionKind,
    CatalogPayload,
    ImageChange,
    PriceChange,
    SaleChange,
)
from telebotshop.domain.models.identity import validate_telegram_id


class CatalogService:
    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self.uow = uow

    async def categories(
        self,
        actor_id: int,
        parent_code: str | None = None,
        page: int = 0,
    ) -> Page[Category]:
        validate_telegram_id(actor_id)
        validate_page(page)
        async with self.uow() as work:
            if parent_code is not None:
                validate_code(parent_code)
                if await work.catalog.category(parent_code) is None:
                    raise CatalogNotFound("Category unavailable")
            return await work.catalog.categories(parent_code, page)

    async def products(
        self,
        actor_id: int,
        category_code: str | None = None,
        query: str | None = None,
        page: int = 0,
    ) -> Page[Product]:
        validate_telegram_id(actor_id)
        validate_page(page)
        if category_code is not None:
            validate_code(category_code)
        if query is not None:
            validate_search(query)
        async with self.uow() as work:
            if category_code is not None and await work.catalog.category(category_code) is None:
                raise CatalogNotFound("Category unavailable")
            return await work.catalog.products(category_code, query, page, public_only=True)

    async def product(self, actor_id: int, product_id: UUID) -> Product:
        validate_telegram_id(actor_id)
        if not isinstance(product_id, UUID):
            raise InvalidInput("Invalid product reference")
        async with self.uow() as work:
            product = await work.catalog.product(product_id)
            if product is None or product.sale_state != SaleState.ACTIVE:
                raise CatalogNotFound("Product unavailable")
            return product

    async def admin_products(self, actor_id: int, page: int = 0) -> Page[Product]:
        validate_page(page)
        async with self.uow() as work:
            await require_permission(work, actor_id, Permission.MANAGE_CATALOG)
            return await work.catalog.products(None, None, page, public_only=False)

    async def admin_product(self, actor_id: int, sku: str) -> Product:
        validate_code(sku)
        async with self.uow() as work:
            await require_permission(work, actor_id, Permission.MANAGE_CATALOG)
            product = await work.catalog.product_by_sku(sku)
            if product is None:
                raise CatalogNotFound("Product unavailable")
            return product

    async def category(self, actor_id: int, code: str) -> Category:
        validate_telegram_id(actor_id)
        validate_code(code)
        async with self.uow() as work:
            category = await work.catalog.category(code)
            if category is None:
                raise CatalogNotFound("Category unavailable")
            return category

    async def prepare_category(self, actor_id: int, draft: CategoryDraft) -> CatalogAction:
        if not isinstance(draft, CategoryDraft):
            raise InvalidInput("Invalid category")
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_CATALOG)
            await self._category_parent(work, draft)
            if await work.catalog.category(draft.code) is not None:
                raise InvalidAction("Category already exists")
            return await self._save(
                work, actor.user_id, CatalogActionKind.CATEGORY, uuid4(), draft, 0
            )

    async def prepare_product(self, actor_id: int, draft: ProductDraft) -> CatalogAction:
        if not isinstance(draft, ProductDraft):
            raise InvalidInput("Invalid product")
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_CATALOG)
            if await work.catalog.category(draft.category_code) is None:
                raise InvalidAction("Category unavailable")
            existing = await work.catalog.product_by_sku(draft.sku)
            if existing is not None and existing.draft.product_type != draft.product_type:
                raise InvalidAction("Product type is immutable")
            return await self._save(
                work,
                actor.user_id,
                CatalogActionKind.CREATE if existing is None else CatalogActionKind.EDIT,
                uuid4() if existing is None else existing.id,
                draft,
                0 if existing is None else existing.version,
            )

    async def prepare_price(self, actor_id: int, sku: str, price_stars: int) -> CatalogAction:
        return await self._prepare_change(
            actor_id, sku, CatalogActionKind.PRICE, PriceChange(price_stars)
        )

    async def prepare_sale(self, actor_id: int, sku: str, state: SaleState) -> CatalogAction:
        return await self._prepare_change(actor_id, sku, CatalogActionKind.SALE, SaleChange(state))

    async def prepare_image(self, actor_id: int, sku: str, image_id: str | None) -> CatalogAction:
        return await self._prepare_change(
            actor_id, sku, CatalogActionKind.IMAGE, ImageChange(image_id)
        )

    async def _prepare_change(
        self,
        actor_id: int,
        sku: str,
        kind: CatalogActionKind,
        payload: CatalogPayload,
    ) -> CatalogAction:
        validate_code(sku)
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_CATALOG)
            product = await work.catalog.product_by_sku(sku)
            if product is None:
                raise CatalogNotFound("Product unavailable")
            return await self._save(work, actor.user_id, kind, product.id, payload, product.version)

    async def confirm(self, actor_id: int, action_id: UUID) -> bool:
        if not isinstance(action_id, UUID):
            raise InvalidInput("Invalid action")
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_CATALOG)
            action = await work.catalog.action(action_id)
            if action is None or action.actor_id != actor.user_id:
                raise InvalidAction("Invalid action")
            if action.status == ActionStatus.APPLIED:
                return False
            if action.status != ActionStatus.PENDING or action.expires_at <= datetime.now(UTC):
                raise InvalidAction("Invalid action")
            changes: dict[str, str | int | None] = {}
            if action.kind == CatalogActionKind.CATEGORY and isinstance(
                action.payload, CategoryDraft
            ):
                await self._category_parent(work, action.payload)
                changes["category_code"] = action.payload.code
            elif action.kind == CatalogActionKind.CREATE and isinstance(
                action.payload, ProductDraft
            ):
                if await work.catalog.category(action.payload.category_code) is None:
                    raise InvalidAction("Category unavailable")
                changes["price_after"] = action.payload.price_stars
            else:
                current = await work.catalog.product(action.target_id, for_update=True)
                if current is None or current.version != action.expected_version:
                    raise InvalidAction("Stale product action")
                changes = {"version_before": current.version, "version_after": current.version + 1}
                if isinstance(action.payload, ProductDraft):
                    if (
                        action.payload.sku != current.draft.sku
                        or action.payload.product_type != current.draft.product_type
                        or await work.catalog.category(action.payload.category_code) is None
                    ):
                        raise InvalidAction("Invalid product change")
                    changes.update(
                        price_before=current.draft.price_stars,
                        price_after=action.payload.price_stars,
                    )
                elif isinstance(action.payload, PriceChange):
                    changes.update(
                        price_before=current.draft.price_stars,
                        price_after=action.payload.price_stars,
                    )
                elif isinstance(action.payload, SaleChange):
                    changes.update(
                        state_before=current.sale_state, state_after=action.payload.sale_state
                    )
                elif isinstance(action.payload, ImageChange):
                    changes["public_photo"] = (
                        "cleared" if action.payload.image_file_id is None else "changed"
                    )
            if not await work.catalog.apply(action):
                raise InvalidAction("Catalog changed")
            await work.catalog.audit(action, changes)
            await work.catalog.complete(action)
            await work.commit()
            return True

    async def cancel(self, actor_id: int, action_id: UUID) -> None:
        if not isinstance(action_id, UUID):
            raise InvalidInput("Invalid action")
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_CATALOG)
            action = await work.catalog.action(action_id)
            if action is None or action.actor_id != actor.user_id:
                raise InvalidAction("Invalid action")
            if action.status == ActionStatus.PENDING:
                await work.catalog.complete(action, cancelled=True)
                await work.commit()

    @staticmethod
    async def _category_parent(work: UnitOfWork, draft: CategoryDraft) -> None:
        if draft.parent_code is not None:
            parent = await work.catalog.category(draft.parent_code)
            if parent is None or parent.draft.parent_code is not None:
                raise InvalidAction("Parent must be a root category")

    @staticmethod
    async def _save(
        work: UnitOfWork,
        actor_id: UUID,
        kind: CatalogActionKind,
        target_id: UUID,
        payload: CatalogPayload,
        version: int,
    ) -> CatalogAction:
        action = CatalogAction(
            uuid4(),
            actor_id,
            kind,
            target_id,
            payload,
            version,
            datetime.now(UTC) + timedelta(minutes=10),
        )
        await work.catalog.save_action(action)
        await work.commit()
        return action
