from datetime import timedelta
from functools import partial
from uuid import uuid4

import pytest

from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.history import HistoryService
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.errors import AccessDenied, InvalidInput
from telebotshop.domain.models.identity import CustomerIdentity
from telebotshop.domain.models.inventory import ImportFormat
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.fulfillment import paid_work
from tests.inventory import inventory_service
from tests.orders import historical_order, order_service


@pytest.mark.asyncio
async def test_history_owns_rows_and_detail_and_uses_price_snapshot(migrated_database):
    engine = migrated_database
    order, work_id = await paid_work(engine)
    factory = partial(unit_of_work, engine)
    history = HistoryService(factory)
    catalog = CatalogService(factory)
    changed = await catalog.prepare_price(9001, order.sku, 333)
    await catalog.confirm(9001, changed.id)
    await IdentityService(factory).get_or_create_customer(CustomerIdentity(102, None, "Other"))
    assert (await history.list(102)).items == ()
    with pytest.raises(AccessDenied):
        await history.detail(102, order.id)
    with pytest.raises(AccessDenied):
        await history.detail(101, uuid4())
    view = await history.detail(101, order.id)
    assert (view.id, view.title, view.price_stars, view.work_id) == (
        order.id,
        order.title,
        order.price_stars,
        work_id,
    )
    assert not view.can_resend
    assert not hasattr(view, "payload") and not hasattr(view, "ciphertext")
    with pytest.raises(AccessDenied):
        await history.list(101, staff=True)
    with pytest.raises(AccessDenied):
        await history.dashboard(101)


@pytest.mark.asyncio
async def test_history_pages_and_staff_dashboard_are_real(migrated_database):
    engine = migrated_database
    order, _ = await paid_work(engine)
    inventory = inventory_service(engine)
    action = await inventory.prepare_import(
        9001,
        order.sku,
        b"extra-0\nextra-1\nextra-2\nextra-3\nextra-4\nextra-5\nextra-6",
        ImportFormat.TXT,
    )
    await inventory.confirm(9001, action.id)
    for _ in range(6):
        await order_service(engine).create(101, order.product_id, uuid4())
    catalog = CatalogService(partial(unit_of_work, engine))
    product = await catalog.admin_product(9001, order.sku)
    await historical_order(engine, product, age=timedelta(days=31))
    history = HistoryService(partial(unit_of_work, engine))
    first = await history.list(101)
    second = await history.list(101, page=1)
    assert len(first.items) == 5 and first.has_more
    assert len(second.items) == 3 and not second.has_more
    assert {item.id for item in first.items}.isdisjoint(item.id for item in second.items)
    assert (await history.list(9001, staff=True)).items == first.items
    dashboard = await history.dashboard(9001)
    assert dashboard.total == 7 and dashboard.paid_stars == order.price_stars
    assert dict(dashboard.status_counts) == {"AWAITING_PAYMENT": 6, "PAID": 1}
    for value in (-1, True, 10000):
        with pytest.raises(InvalidInput):
            await history.list(101, page=value)
