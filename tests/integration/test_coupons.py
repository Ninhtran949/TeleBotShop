from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text

from telebotshop.application.services.coupons import CouponService
from telebotshop.domain.errors import AccessDenied, InvalidAction
from telebotshop.domain.models.coupons import CouponDraft, DiscountKind
from telebotshop.domain.models.wallet import WalletPermission
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.integration.test_wallet_v2_ledger import wallet_fixture
from tests.refunds import refund_staff


def draft(**changes):
    return CouponDraft(
        **(
            dict(
                code="FAKE10",
                kind=DiscountKind.PERCENT,
                value=10,
                expires_at=datetime.now(UTC) + timedelta(days=1),
                usage_cap=2,
                per_user_limit=1,
            )
            | changes
        )
    )


def coupons(engine):
    return CouponService(partial(unit_of_work, engine))


@pytest.mark.asyncio
async def test_coupon_management_is_audited_immutable_and_idempotent(migrated_database):
    engine = migrated_database
    await wallet_fixture(engine)
    service = coupons(engine)
    action, change = uuid4(), uuid4()
    config = draft()
    created = await service.create(9001, config, action)
    assert await service.create(9001, config, action) == created
    assert (await service.list(9001))[0] == created
    await service.set_active(9001, "fake10", False, change, expected_active=True)
    await service.set_active(9001, "FAKE10", True, uuid4(), expected_active=False)
    await service.set_active(9001, "FAKE10", False, change, expected_active=True)
    assert (await service.list(9001))[0].active is True
    with pytest.raises(InvalidAction):
        await service.create(9001, draft(value=20), action)
    with pytest.raises(InvalidAction):
        await service.create(9001, config, uuid4())
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM coupons")) == 1
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action LIKE 'coupon.%'")
            )
            == 3
        )


@pytest.mark.asyncio
async def test_coupon_management_requires_current_financial_price_grant(migrated_database):
    engine = migrated_database
    wallet = await wallet_fixture(engine)
    await refund_staff(engine, 202)
    service = coupons(engine)
    for actor in (101, 202):
        with pytest.raises(AccessDenied):
            await service.create(actor, draft(), uuid4())
        with pytest.raises(AccessDenied):
            await service.list(actor)
    await wallet.grant_permission(9001, 202, WalletPermission.PRICE, True, "FAKE price grant")
    await service.create(202, draft(), uuid4())
    await wallet.grant_permission(9001, 202, WalletPermission.PRICE, False, "FAKE revoke")
    with pytest.raises(AccessDenied):
        await service.set_active(202, "FAKE10", False, uuid4(), expected_active=True)
    assert (await service.list(9001))[0].active is True


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["expired", "product", "category"])
async def test_coupon_creation_rejects_invalid_server_scope_or_expiry(migrated_database, problem):
    await wallet_fixture(migrated_database)
    changes = (
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        if problem == "expired"
        else ({"product_id": uuid4()} if problem == "product" else {"category_code": "MISSING"})
    )
    with pytest.raises(InvalidAction):
        await coupons(migrated_database).create(9001, draft(**changes), uuid4())
    assert await coupons(migrated_database).list(9001) == ()
