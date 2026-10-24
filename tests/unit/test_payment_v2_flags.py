from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from telebotshop.application.services.checkout import CheckoutService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.pre_checkout import PreCheckout
from telebotshop.domain.models.support import PublicTerms
from telebotshop.infrastructure.config import PaymentV2Settings


def settings(**changes):
    return PaymentV2Settings(
        app_env="test",
        database_url="postgresql+asyncpg://fake:fake@127.0.0.1:55432/telebotshop_payment_v2_sandbox",
        **changes,
    )


def test_payment_v2_defaults_to_paused_stars_and_sandbox_main_rails():
    value = settings()
    assert not value.stars_checkout_enabled
    assert value.credit_wallet_enabled and value.bank_topup_enabled and value.usdt_topup_enabled
    assert not value.external_payments_live_enabled and not value.binance_readonly_enabled


@pytest.mark.parametrize(
    "changes",
    [
        {"external_payments_live_enabled": True},
        {"binance_readonly_enabled": True},
        {"database_url": "postgresql+asyncpg://fake:fake@127.0.0.1:55432/telebotshop_dev"},
        {"app_env": "production"},
    ],
)
def test_sandbox_configuration_cannot_target_main_data_or_enable_real_money(changes):
    base = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://fake:fake@127.0.0.1:55432/telebotshop_payment_v2_sandbox",
    }
    with pytest.raises(ValidationError):
        PaymentV2Settings(**(base | changes))


@pytest.mark.asyncio
async def test_disabled_stars_ui_paths_fail_before_database_or_invoice_effect():
    def unavailable():
        raise AssertionError("Disabled checkout accessed database")

    sender = AsyncMock()
    service = CheckoutService(unavailable, sender, PublicTerms("FAKE terms"), enabled=False)
    with pytest.raises(InvalidAction):
        await service.prepare(101, uuid4(), uuid4())
    with pytest.raises(InvalidAction):
        await service.pay(101, uuid4(), service.terms_version)
    sender.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_paused_pre_checkout_rejects_direct_call_before_database():
    def unavailable():
        raise AssertionError("Paused pre-checkout accessed database")

    service = PreCheckoutService(unavailable, enabled=False)
    assert await service.check(PreCheckout("FAKE-query", 101, "XTR", 100, "x" * 32)) is False
