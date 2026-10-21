import hmac
from uuid import UUID

from sqlalchemy import RowMapping, Select, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.inventory import InventoryRepository
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import PAGE_SIZE, Page, Product, SaleState
from telebotshop.domain.models.inventory import (
    FINITE_TYPES,
    CryptoProof,
    InventoryAction,
    InventoryActionKind,
    InventoryCryptoError,
    InventoryState,
    InventoryUnitView,
    SealedPayload,
    SealedUnit,
    StockPage,
    StockSummary,
)
from telebotshop.infrastructure.database.models.catalog import products
from telebotshop.infrastructure.database.models.identity import audit_logs
from telebotshop.infrastructure.database.models.inventory import (
    inventory_actions,
    inventory_crypto_state,
    inventory_units,
)


def unit_record(unit: SealedUnit) -> dict[str, str | int]:
    return {
        "id": str(unit.id),
        "row": unit.source_row,
        "fingerprint": unit.fingerprint.hex(),
        "key_version": unit.sealed.key_version,
        "nonce": unit.sealed.nonce.hex(),
        "ciphertext": unit.sealed.ciphertext.hex(),
    }


def unit_from_record(record: object) -> SealedUnit:
    try:
        if not isinstance(record, dict) or set(record) != {
            "id",
            "row",
            "fingerprint",
            "key_version",
            "nonce",
            "ciphertext",
        }:
            raise ValueError
        return SealedUnit(
            UUID(record["id"]),
            record["row"],
            bytes.fromhex(record["fingerprint"]),
            SealedPayload(
                record["key_version"],
                bytes.fromhex(record["nonce"]),
                bytes.fromhex(record["ciphertext"]),
            ),
        )
    except (ValueError, TypeError, AttributeError):
        raise InventoryCryptoError("Invalid pending encrypted inventory") from None


def stock_query() -> Select:
    counts = (
        select(
            inventory_units.c.product_id,
            func.count()
            .filter(inventory_units.c.status == InventoryState.AVAILABLE)
            .label("available"),
            func.count()
            .filter(inventory_units.c.status == InventoryState.RETIRED)
            .label("retired"),
        )
        .group_by(inventory_units.c.product_id)
        .subquery()
    )
    return (
        select(
            products.c.id,
            products.c.sku,
            products.c.sale_state,
            func.coalesce(counts.c.available, 0).label("available"),
            func.coalesce(counts.c.retired, 0).label("retired"),
        )
        .select_from(products.outerjoin(counts, counts.c.product_id == products.c.id))
        .where(products.c.product_type.in_(FINITE_TYPES))
    )


def stock_summary(row: RowMapping, threshold: int) -> StockSummary:
    return StockSummary(
        row["id"],
        row["sku"],
        row["available"],
        row["retired"],
        row["sale_state"] == SaleState.ACTIVE and row["available"] <= threshold,
    )


class SqlInventoryRepository(InventoryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check_keys(self, proof: CryptoProof) -> None:
        incoming = {str(version): value.hex() for version, value in proof.keys}
        await self.session.execute(
            pg_insert(inventory_crypto_state)
            .values(id=1, dedup_verifier=proof.dedup, key_verifiers=incoming)
            .on_conflict_do_nothing(index_elements=[inventory_crypto_state.c.id])
        )
        # shortcut: key registration serializes writes; narrow locks when contention warrants it.
        row = (
            (
                await self.session.execute(
                    select(inventory_crypto_state)
                    .where(inventory_crypto_state.c.id == 1)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        stored = row["key_verifiers"]
        if not hmac.compare_digest(row["dedup_verifier"], proof.dedup) or any(
            version not in incoming
            or not isinstance(value, str)
            or not value.isascii()
            or not hmac.compare_digest(value, incoming[version])
            for version, value in stored.items()
        ):
            raise InventoryCryptoError("Inventory keys differ from the registered configuration")
        if stored != incoming:
            await self.session.execute(
                update(inventory_crypto_state)
                .where(inventory_crypto_state.c.id == 1)
                .values(key_verifiers=incoming)
            )

    async def save_action(self, action: InventoryAction, units: tuple[SealedUnit, ...]) -> None:
        await self.session.execute(
            insert(inventory_actions).values(
                id=action.id,
                actor_id=action.actor_id,
                product_id=action.product_id,
                unit_id=action.unit_id,
                kind=action.kind,
                quantity=action.quantity,
                envelopes=[unit_record(unit) for unit in units] if units else None,
                expires_at=action.expires_at,
            )
        )

    async def action(self, action_id: UUID) -> InventoryAction | None:
        row = (
            (
                await self.session.execute(
                    select(
                        inventory_actions.c.id,
                        inventory_actions.c.actor_id,
                        inventory_actions.c.kind,
                        inventory_actions.c.product_id,
                        inventory_actions.c.unit_id,
                        inventory_actions.c.quantity,
                        inventory_actions.c.expires_at,
                        inventory_actions.c.status,
                    )
                    .where(inventory_actions.c.id == action_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return InventoryAction(
            row["id"],
            row["actor_id"],
            InventoryActionKind(row["kind"]),
            row["product_id"],
            row["unit_id"],
            row["quantity"],
            row["expires_at"],
            ActionStatus(row["status"]),
        )

    async def pending_units(self, action_id: UUID) -> tuple[SealedUnit, ...]:
        records = await self.session.scalar(
            select(inventory_actions.c.envelopes).where(inventory_actions.c.id == action_id)
        )
        if not isinstance(records, list):
            raise InventoryCryptoError("Invalid pending encrypted inventory")
        return tuple(unit_from_record(record) for record in records)

    async def add_units(self, product: Product, units: tuple[SealedUnit, ...]) -> int | None:
        statement = (
            pg_insert(inventory_units)
            .values(
                [
                    {
                        "id": unit.id,
                        "product_id": product.id,
                        "product_type": product.draft.product_type,
                        "fingerprint": unit.fingerprint,
                        "key_version": unit.sealed.key_version,
                        "nonce": unit.sealed.nonce,
                        "ciphertext": unit.sealed.ciphertext,
                    }
                    for unit in sorted(units, key=lambda item: item.fingerprint)
                ]
            )
            .on_conflict_do_nothing(constraint="uq_inventory_product_fingerprint")
            .returning(inventory_units.c.fingerprint)
        )
        inserted = set((await self.session.execute(statement)).scalars())
        return next((unit.source_row for unit in units if unit.fingerprint not in inserted), None)

    async def unit(self, unit_id: UUID) -> InventoryUnitView | None:
        row = (
            await self.session.execute(
                select(
                    inventory_units.c.id, inventory_units.c.product_id, inventory_units.c.status
                ).where(inventory_units.c.id == unit_id)
            )
        ).one_or_none()
        return (
            None
            if row is None
            else InventoryUnitView(row.id, row.product_id, InventoryState(row.status))
        )

    async def retire(self, product_id: UUID, unit_id: UUID) -> bool:
        result = await self.session.scalar(
            update(inventory_units)
            .where(
                inventory_units.c.id == unit_id,
                inventory_units.c.product_id == product_id,
                inventory_units.c.status == InventoryState.AVAILABLE,
            )
            .values(status=InventoryState.RETIRED)
            .returning(inventory_units.c.id)
        )
        return result is not None

    async def stock(self, product: Product, page: int, threshold: int) -> StockPage:
        row = (
            (await self.session.execute(stock_query().where(products.c.id == product.id)))
            .mappings()
            .one()
        )
        units = (
            await self.session.execute(
                select(inventory_units.c.id, inventory_units.c.product_id, inventory_units.c.status)
                .where(inventory_units.c.product_id == product.id)
                .order_by(inventory_units.c.id)
                .limit(PAGE_SIZE + 1)
                .offset(page * PAGE_SIZE)
            )
        ).all()
        return StockPage(
            stock_summary(row, threshold),
            Page(
                tuple(
                    InventoryUnitView(unit.id, unit.product_id, InventoryState(unit.status))
                    for unit in units[:PAGE_SIZE]
                ),
                page,
                len(units) > PAGE_SIZE,
            ),
        )

    async def low_stock(self, page: int, threshold: int) -> Page[StockSummary]:
        query = stock_query().subquery()
        rows = (
            (
                await self.session.execute(
                    select(query)
                    .where(
                        query.c.sale_state == SaleState.ACTIVE,
                        query.c.available <= threshold,
                    )
                    .order_by(query.c.sku)
                    .limit(PAGE_SIZE + 1)
                    .offset(page * PAGE_SIZE)
                )
            )
            .mappings()
            .all()
        )
        return Page(
            tuple(stock_summary(row, threshold) for row in rows[:PAGE_SIZE]),
            page,
            len(rows) > PAGE_SIZE,
        )

    async def complete(self, action: InventoryAction, *, cancelled: bool = False) -> None:
        await self.session.execute(
            update(inventory_actions)
            .where(inventory_actions.c.id == action.id)
            .values(
                status=ActionStatus.CANCELLED if cancelled else ActionStatus.APPLIED,
                completed_at=func.clock_timestamp(),
                envelopes=None,
            )
        )

    async def audit(self, action: InventoryAction) -> None:
        await self.session.execute(
            insert(audit_logs).values(
                actor_id=action.actor_id,
                action=action.kind,
                target_id=action.unit_id
                if action.kind == InventoryActionKind.RETIRE
                else action.product_id,
                correlation_id=action.id,
                changes={
                    "product_id": str(action.product_id),
                    "quantity": action.quantity,
                    "operation": "retired"
                    if action.kind == InventoryActionKind.RETIRE
                    else "imported",
                },
            )
        )
