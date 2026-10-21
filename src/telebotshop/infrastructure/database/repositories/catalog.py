from dataclasses import asdict
from uuid import UUID

from sqlalchemy import RowMapping, Select, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.catalog import CatalogRepository
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import (
    PAGE_SIZE,
    Category,
    CategoryDraft,
    Page,
    Product,
    ProductDraft,
    ProductType,
    SaleState,
    metadata_from_record,
)
from telebotshop.domain.models.catalog_action import (
    CatalogAction,
    CatalogActionKind,
    ImageChange,
    PriceChange,
    SaleChange,
    payload_from_record,
)
from telebotshop.infrastructure.database.models.catalog import catalog_actions, categories, products
from telebotshop.infrastructure.database.models.identity import audit_logs


def category_query() -> Select:
    parent = categories.alias("parent_category")
    return select(
        categories.c.id, categories.c.code, categories.c.name, parent.c.code.label("parent_code")
    ).select_from(categories.outerjoin(parent, categories.c.parent_id == parent.c.id))


def product_query() -> Select:
    return select(
        products.c.id,
        products.c.sku,
        products.c.product_type,
        products.c.name,
        products.c.description,
        products.c.price_stars,
        products.c.public_metadata,
        products.c.sale_state,
        products.c.version,
        products.c.image_file_id,
        categories.c.code.label("category_code"),
    ).select_from(products.join(categories, products.c.category_id == categories.c.id))


def product_from_row(row: RowMapping) -> Product:
    kind = ProductType(row["product_type"])
    draft = ProductDraft(
        row["sku"],
        row["category_code"],
        kind,
        row["name"],
        row["description"],
        row["price_stars"],
        metadata_from_record(kind, row["public_metadata"]),
    )
    return Product(
        row["id"], draft, SaleState(row["sale_state"]), row["version"], row["image_file_id"]
    )


def product_values(draft: ProductDraft) -> dict[str, object]:
    return {
        "sku": draft.sku,
        "category_id": select(categories.c.id)
        .where(categories.c.code == draft.category_code)
        .scalar_subquery(),
        "product_type": draft.product_type,
        "name": draft.name,
        "description": draft.description,
        "price_stars": draft.price_stars,
        "public_metadata": asdict(draft.metadata),
    }


class SqlCatalogRepository(CatalogRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def category(self, code: str) -> Category | None:
        row = (
            (await self.session.execute(category_query().where(categories.c.code == code)))
            .mappings()
            .one_or_none()
        )
        return (
            None
            if row is None
            else Category(row["id"], CategoryDraft(row["code"], row["name"], row["parent_code"]))
        )

    async def categories(self, parent_code: str | None, page: int) -> Page[Category]:
        statement = category_query()
        if parent_code is None:
            statement = statement.where(categories.c.parent_id.is_(None))
        else:
            statement = statement.where(
                categories.c.parent_id
                == select(categories.c.id).where(categories.c.code == parent_code).scalar_subquery()
            )
        rows = (
            (
                await self.session.execute(
                    statement.order_by(categories.c.code)
                    .limit(PAGE_SIZE + 1)
                    .offset(page * PAGE_SIZE)
                )
            )
            .mappings()
            .all()
        )
        values = tuple(
            Category(row["id"], CategoryDraft(row["code"], row["name"], row["parent_code"]))
            for row in rows[:PAGE_SIZE]
        )
        return Page(values, page, len(rows) > PAGE_SIZE)

    async def product(self, product_id: UUID, *, for_update: bool = False) -> Product | None:
        statement = product_query().where(products.c.id == product_id)
        if for_update:
            statement = statement.with_for_update(of=products)
        row = (await self.session.execute(statement)).mappings().one_or_none()
        return None if row is None else product_from_row(row)

    async def product_by_sku(self, sku: str) -> Product | None:
        row = (
            (await self.session.execute(product_query().where(products.c.sku == sku)))
            .mappings()
            .one_or_none()
        )
        return None if row is None else product_from_row(row)

    async def products(
        self,
        category_code: str | None,
        query: str | None,
        page: int,
        *,
        public_only: bool,
    ) -> Page[Product]:
        statement = product_query()
        if public_only:
            statement = statement.where(products.c.sale_state == SaleState.ACTIVE)
        if category_code is not None:
            parent = (
                select(categories.c.id).where(categories.c.code == category_code).scalar_subquery()
            )
            category_ids = select(categories.c.id).where(
                or_(categories.c.code == category_code, categories.c.parent_id == parent)
            )
            statement = statement.where(products.c.category_id.in_(category_ids))
        if query is not None:
            statement = statement.where(
                or_(
                    products.c.name.icontains(query, autoescape=True),
                    products.c.description.icontains(query, autoescape=True),
                    products.c.sku.icontains(query, autoescape=True),
                )
            )
        rows = (
            (
                await self.session.execute(
                    statement.order_by(products.c.sku).limit(PAGE_SIZE + 1).offset(page * PAGE_SIZE)
                )
            )
            .mappings()
            .all()
        )
        return Page(
            tuple(product_from_row(row) for row in rows[:PAGE_SIZE]), page, len(rows) > PAGE_SIZE
        )

    async def save_action(self, action: CatalogAction) -> None:
        # shortcut: retain expired intents until Owner approves a retention policy and cleanup.
        await self.session.execute(
            insert(catalog_actions).values(
                id=action.id,
                actor_id=action.actor_id,
                kind=action.kind,
                target_id=action.target_id,
                payload=asdict(action.payload),
                expected_version=action.expected_version,
                expires_at=action.expires_at,
            )
        )

    async def action(self, action_id: UUID) -> CatalogAction | None:
        row = (
            (
                await self.session.execute(
                    select(catalog_actions)
                    .where(catalog_actions.c.id == action_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        kind = CatalogActionKind(row["kind"])
        return CatalogAction(
            row["id"],
            row["actor_id"],
            kind,
            row["target_id"],
            payload_from_record(kind, row["payload"]),
            row["expected_version"],
            row["expires_at"],
            ActionStatus(row["status"]),
        )

    async def apply(self, action: CatalogAction) -> bool:
        payload = action.payload
        if action.kind == CatalogActionKind.CATEGORY and isinstance(payload, CategoryDraft):
            parent_id = (
                None
                if payload.parent_code is None
                else select(categories.c.id)
                .where(categories.c.code == payload.parent_code)
                .scalar_subquery()
            )
            statement = (
                pg_insert(categories)
                .values(
                    id=action.target_id, code=payload.code, name=payload.name, parent_id=parent_id
                )
                .on_conflict_do_nothing(constraint="uq_categories_code")
            )
        elif action.kind == CatalogActionKind.CREATE and isinstance(payload, ProductDraft):
            statement = (
                pg_insert(products)
                .values(id=action.target_id, **product_values(payload))
                .on_conflict_do_nothing(constraint="uq_products_sku")
            )
        else:
            values: dict[str, object]
            if isinstance(payload, ProductDraft):
                values = product_values(payload)
            elif isinstance(payload, PriceChange):
                values = {"price_stars": payload.price_stars}
            elif isinstance(payload, SaleChange):
                values = {"sale_state": payload.sale_state}
            elif isinstance(payload, ImageChange):
                values = {"image_file_id": payload.image_file_id}
            else:
                return False
            statement = (
                update(products)
                .where(
                    products.c.id == action.target_id, products.c.version == action.expected_version
                )
                .values(**values, version=products.c.version + 1)
            )
        return (await self.session.execute(statement)).rowcount == 1

    async def complete(self, action: CatalogAction, *, cancelled: bool = False) -> None:
        await self.session.execute(
            update(catalog_actions)
            .where(catalog_actions.c.id == action.id)
            .values(
                status=ActionStatus.CANCELLED if cancelled else ActionStatus.APPLIED,
                completed_at=func.clock_timestamp(),
            )
        )

    async def audit(self, action: CatalogAction, changes: dict[str, str | int | None]) -> None:
        await self.session.execute(
            insert(audit_logs).values(
                actor_id=action.actor_id,
                action=action.kind,
                target_id=action.target_id,
                correlation_id=action.id,
                changes=changes,
            )
        )
