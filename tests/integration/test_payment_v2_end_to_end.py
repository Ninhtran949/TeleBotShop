import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from uuid import uuid4

import pytest
from aiogram.methods import AnswerPreCheckoutQuery, RefundStarPayment, SendInvoice
from sqlalchemy import text

from telebotshop.application.services.notifications import NotificationService
from telebotshop.application.services.payment_reconciliation import PaymentReconciliationService
from telebotshop.application.services.wallet_refunds import WalletRefundService
from telebotshop.bootstrap import runtime_dispatcher
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.notifications import NotificationKind
from telebotshop.domain.models.topups import TopUpState
from telebotshop.infrastructure.config import PaymentV2Settings
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.delivery.finite import FiniteDeliverySender
from telebotshop.infrastructure.payments.binance import NetworkPolicy, SimulatedDepositReader
from tests.fulfillment import fulfillment_service
from tests.integration.test_core_shop import product_fixture
from tests.integration.test_notification_failures import Sender
from tests.integration.test_topups_v2 import bank_evidence, topup_fixture
from tests.integration.test_wallet_v2_checkout import checkout
from tests.inventory import fake_cipher, inventory_service
from tests.telegram import TelegramRecorder, callback_update, message_update


def simulated_reader(now):
    return SimulatedDepositReader(
        [
            {
                "network": "FAKE_TESTNET",
                "txId": "FAKE-PV2-E2E-TX",
                "address": "FAKE-ADDRESS",
                "coin": "USDT",
                "amount": "0.04",
                "status": 1,
                "travelRuleStatus": 0,
                "confirmTimes": "3/3",
                "insertTime": int(now.timestamp() * 1000),
                "sandbox_token_contract": "FAKE_TOKEN",
            }
        ],
        NetworkPolicy("FAKE_TESTNET", "FAKE_TOKEN", 3),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rail", ["BANK_TRANSFER", "USDT_BINANCE"])
async def test_topup_to_credit_ui_purchase_and_shared_delivery_needs_no_stars(
    migrated_database, monkeypatch, rail
):
    engine = migrated_database
    product = await product_fixture(engine, ProductType.UNIQUE_KEY, False)
    wallet, topups = await topup_fixture(engine)
    factory = partial(unit_of_work, engine)
    service = checkout(engine)
    await service.set_price(9001, product.id, 1000, uuid4(), "FAKE credit price")
    if rail == "BANK_TRANSFER":
        intent = await topups.create_bank(101, 1000, uuid4())
        assert (await topups.ingest(bank_evidence(intent))).state == TopUpState.CREDITED
    else:
        await topups.set_sandbox_route(9001, 101, "FAKE_TESTNET", "FAKE_TOKEN", "FAKE-ADDRESS")
        await topups.create_usdt(101, Decimal("0.04"), uuid4())
        await PaymentReconciliationService(factory, topups).run(simulated_reader(datetime.now(UTC)))
    assert (await wallet.balance(101)).available == 1000
    telegram = TelegramRecorder(monkeypatch)

    async def send(bot, method, timeout=None):
        assert not isinstance(method, SendInvoice | AnswerPreCheckoutQuery | RefundStarPayment)
        assert engine.pool.checkedout() == 0
        return await telegram.send(bot, method, timeout)

    from unittest.mock import AsyncMock

    monkeypatch.setattr(telegram.bot.session, "make_request", AsyncMock(side_effect=send))
    settings = PaymentV2Settings(
        app_env="test",
        database_url="postgresql+asyncpg://FAKE:FAKE@127.0.0.1/telebotshop_payment_v2_sandbox",
    )
    dispatcher = runtime_dispatcher(
        factory,
        inventory_service(engine),
        fake_cipher(),
        telegram.bot,
        service_terms(),
        stars_enabled=False,
        payment_v2=settings,
    )

    def view():
        return next(call for call in reversed(telegram.calls) if getattr(call, "text", None))

    def button(prefix):
        return next(
            b.callback_data
            for row in view().reply_markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith(prefix)
        )

    try:
        await dispatcher.feed_update(telegram.bot, message_update("/search Sản phẩm"))
        assert "1,000 credit" in view().text or "1,000 credit" in str(view().reply_markup)
        await dispatcher.feed_update(telegram.bot, callback_update(button("shop:p:")))
        assert "Mua bằng Credit" in str(view().reply_markup)
        assert "buy:" not in str(view().reply_markup) and "Stars" not in view().text
        await dispatcher.feed_update(telegram.bot, callback_update(button("wc:p:")))
        confirm = button("wc:b:")
        await dispatcher.feed_update(telegram.bot, callback_update(confirm))
        await dispatcher.feed_update(telegram.bot, callback_update(confirm))
        assert "Đã thanh toán bằng Credit" in view().text
        worker = fulfillment_service(engine, FiniteDeliverySender(telegram.bot))
        assert await worker.run_once() and not await worker.run_once()
        assert (await wallet.balance(101)).available == 0
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT status FROM orders")) == "DELIVERED"
            assert await connection.scalar(text("SELECT count(*) FROM invoice_intents")) == 0
            assert await connection.scalar(text("SELECT count(*) FROM pre_checkout_approvals")) == 0
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM payments WHERE charge_id IS NOT NULL")
                )
                == 0
            )
            kinds = (
                (
                    await connection.execute(
                        text("SELECT kind FROM wallet_journals ORDER BY created_at")
                    )
                )
                .scalars()
                .all()
            )
            assert kinds == ["TOPUP", "PURCHASE"]
        assert (await wallet.reconcile(9001)).balanced
    finally:
        await telegram.bot.session.close()


def service_terms():
    from telebotshop.domain.models.support import PublicTerms

    return PublicTerms("FAKE sandbox terms only")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["topup", "purchase"])
@pytest.mark.parametrize("lost_ack", [False, True])
async def test_money_commit_failure_and_lost_ack_are_replay_safe(
    migrated_database, operation, lost_ack
):
    engine = migrated_database
    product = await product_fixture(engine, ProductType.UNIQUE_KEY, False)
    wallet, topups = await topup_fixture(engine)
    intent = await topups.create_bank(101, 1000, uuid4())
    event = bank_evidence(intent)
    service = checkout(engine)
    await service.set_price(9001, product.id, 1000, uuid4(), "FAKE price")
    action = uuid4()
    if operation == "purchase":
        await topups.ingest(event)
    target = topups if operation == "topup" else service
    normal = target.uow

    @asynccontextmanager
    async def broken():
        async with unit_of_work(engine) as work:
            commit = work.commit

            async def fail():
                if lost_ack:
                    await commit()
                raise RuntimeError("FAKE commit boundary")

            work.commit = fail
            yield work

    async def apply():
        if operation == "topup":
            return await topups.ingest(event)
        return await service.buy(101, product.id, action, 1000, service.terms_version)

    target.uow = broken
    with pytest.raises(RuntimeError, match="FAKE commit boundary"):
        await apply()
    target.uow = normal
    await apply()
    await apply()
    assert (await wallet.balance(101)).available == (1000 if operation == "topup" else 0)
    assert (await wallet.reconcile(9001)).balanced
    assert len(await wallet.history(101)) == (1 if operation == "topup" else 2)


@pytest.mark.asyncio
async def test_topup_notifications_use_buyer_and_review_owner_metadata_only(migrated_database):
    engine = migrated_database
    _, topups = await topup_fixture(engine)
    intent = await topups.create_bank(101, 1000, uuid4())
    result = await topups.ingest(bank_evidence(intent))
    from dataclasses import replace

    await topups.ingest(replace(bank_evidence(intent), reference="FAKE unmatched"))
    sender = Sender(engine)
    notifications = NotificationService(partial(unit_of_work, engine), sender, threshold=5)
    await notifications.discover()
    while await notifications.run_once():
        pass
    credits = [r for r in sender.requests if r.kind == NotificationKind.WALLET_TOPUP]
    reviews = [r for r in sender.requests if r.kind == NotificationKind.TOPUP_REVIEW]
    assert len(credits) == len(reviews) == 1
    assert credits[0].telegram_id == 101 and credits[0].target_id == result.intent_id
    assert reviews[0].telegram_id == 9001


@pytest.mark.asyncio
async def test_topup_reversal_is_distinct_and_cannot_spend_already_purchased_credit(
    migrated_database,
):
    engine = migrated_database
    product = await product_fixture(engine, ProductType.UNIQUE_KEY, False)
    wallet, topups = await topup_fixture(engine)
    intent = await topups.create_bank(101, 1000, uuid4())
    await topups.ingest(bank_evidence(intent))
    service = checkout(engine)
    await service.set_price(9001, product.id, 1000, uuid4(), "FAKE price")
    order = await service.buy(101, product.id, uuid4(), 1000, service.terms_version)
    refunds = WalletRefundService(partial(unit_of_work, engine))
    with pytest.raises(InvalidAction):
        await refunds.reverse_topup(9001, intent.id, "FAKE source reversal", "FAKE bank report")
    action = await refunds.prepare(9001, order.id, uuid4(), "FAKE order refund", "FAKE support")
    a, b = await asyncio.gather(refunds.confirm(9001, action), refunds.confirm(9001, action))
    assert a == b and (await wallet.balance(101)).available == 1000
    a = await refunds.reverse_topup(9001, intent.id, "FAKE source reversal", "FAKE bank report")
    b = await refunds.reverse_topup(9001, intent.id, "FAKE source reversal", "FAKE bank report")
    assert a == b and (await wallet.balance(101)).available == 0
    assert (await wallet.reconcile(9001)).balanced
