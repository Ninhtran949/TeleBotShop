import base64
import json
from functools import partial

from sqlalchemy.ext.asyncio import AsyncEngine

from telebotshop.application.services.inventory import InventoryService
from telebotshop.domain.models.catalog import Product, SaleState
from telebotshop.infrastructure.config import InventorySettings
from telebotshop.infrastructure.crypto import AesGcmInventoryCipher
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.catalog import fake_product, setup_catalog


def fake_inventory_settings() -> InventorySettings:
    return InventorySettings(
        inventory_encryption_keys=json.dumps({"1": base64.b64encode(b"a" * 32).decode()}),
        inventory_current_key_version=1,
        inventory_dedup_key=base64.b64encode(b"d" * 32).decode(),
        low_stock_threshold=5,
    )


def fake_cipher() -> AesGcmInventoryCipher:
    return AesGcmInventoryCipher({1: b"a" * 32}, 1, b"d" * 32)


def inventory_service(database: AsyncEngine, *, threshold: int = 5) -> InventoryService:
    return InventoryService(partial(unit_of_work, database), fake_cipher(), threshold)


async def setup_inventory(database: AsyncEngine) -> Product:
    catalog = await setup_catalog(database)
    action = await catalog.prepare_product(9001, fake_product())
    await catalog.confirm(9001, action.id)
    publish = await catalog.prepare_sale(9001, "DEMO-1", SaleState.ACTIVE)
    await catalog.confirm(9001, publish.id)
    return await catalog.admin_product(9001, "DEMO-1")
