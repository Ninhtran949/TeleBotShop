import argparse
import asyncio
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import uvicorn
from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TEST
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from telebotshop.admin.catalog import create_catalog_admin_router
from telebotshop.admin.identity import create_admin_router
from telebotshop.admin.inventory import create_inventory_admin_router
from telebotshop.admin.manual import create_manual_admin_router
from telebotshop.admin.payment_operator import OperatorServices, run_operator
from telebotshop.admin.refunds import create_refund_admin_router
from telebotshop.admin.resources import create_resource_admin_router
from telebotshop.admin.wallet import create_wallet_admin_router
from telebotshop.api.bank_webhook import create_bank_webhook_router
from telebotshop.api.health import create_api
from telebotshop.api.webhook import create_webhook_router
from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.checkout import CheckoutService
from telebotshop.application.services.fulfillment import FulfillmentService
from telebotshop.application.services.history import HistoryService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.inventory import InventoryService
from telebotshop.application.services.manual import ManualFulfillmentService
from telebotshop.application.services.notifications import NotificationService
from telebotshop.application.services.orders import OrderService
from telebotshop.application.services.payment_reconciliation import PaymentReconciliationService
from telebotshop.application.services.payments import PaymentService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.application.services.refunds import RefundService
from telebotshop.application.services.resends import ResendService
from telebotshop.application.services.resources import ResourceService
from telebotshop.application.services.support import SupportService
from telebotshop.application.services.topup_review import ManualPaymentReviewService
from telebotshop.application.services.topups import TopUpService
from telebotshop.application.services.wallet import WalletLedgerService
from telebotshop.application.services.wallet_checkout import WalletCheckoutService
from telebotshop.application.services.wallet_refunds import WalletRefundService
from telebotshop.bot.routers.catalog import create_catalog_router
from telebotshop.bot.routers.checkout import create_checkout_router
from telebotshop.bot.routers.identity import create_customer_router
from telebotshop.bot.routers.orders import create_order_router
from telebotshop.bot.routers.support import create_support_router
from telebotshop.bot.routers.wallet import create_wallet_router
from telebotshop.domain.errors import (
    AccessDenied,
    InvalidAction,
    InvalidInput,
    OwnerConfigurationError,
)
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.domain.models.support import PublicTerms
from telebotshop.domain.models.topups import BankAccount, TopUpIntent
from telebotshop.infrastructure.config import (
    BotSettings,
    InventorySettings,
    PaymentV2Settings,
    Settings,
    WebhookSettings,
    WorkerSettings,
)
from telebotshop.infrastructure.crypto import AesGcmInventoryCipher
from telebotshop.infrastructure.database.readiness import database_ready, payment_sandbox_ready
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.delivery.finite import FiniteDeliverySender
from telebotshop.infrastructure.payments.bank import SePaySandboxReader
from telebotshop.infrastructure.payments.binance import (
    BinanceDepositReader,
    NetworkPolicy,
    SimulatedDepositReader,
)
from telebotshop.infrastructure.payments.sepay import SePayWebhookVerifier, bank_qr_url
from telebotshop.infrastructure.support import TermsConfigurationError, load_terms
from telebotshop.infrastructure.telegram.guard import IngressGuard
from telebotshop.infrastructure.telegram.invoices import StarsInvoiceSender
from telebotshop.infrastructure.telegram.logging import configure_telegram_logging
from telebotshop.infrastructure.telegram.notifications import TelegramNotificationSender
from telebotshop.infrastructure.telegram.payments import PaymentsDispatcher, create_payment_router
from telebotshop.infrastructure.telegram.pre_checkout import create_pre_checkout_router
from telebotshop.infrastructure.telegram.refunds import TelegramRefundGateway
from telebotshop.infrastructure.telegram.webhook import dispatch_update
from telebotshop.workers.fulfillment import run_forever


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = Settings() if settings is None else settings
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"timeout": 2},
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await engine.dispose()

    return create_api(partial(database_ready, engine), lifespan=lifespan)


class PollingStartupError(Exception):
    pass


def create_bot(settings: BotSettings) -> Bot:
    return Bot(
        settings.bot_token.get_secret_value(),
        session=AiohttpSession(api=TEST if settings.telegram_environment == "test" else PRODUCTION),
    )


class ExpiryStartupError(Exception):
    pass


async def run_expiry(settings: Settings) -> tuple[int, int]:
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"timeout": 2},
    )
    try:
        if not await database_ready(engine):
            raise ExpiryStartupError("Database unavailable or schema outdated")
        orders = OrderService(partial(unit_of_work, engine))
        return await orders.expire(), await orders.recover_invoices()
    finally:
        await engine.dispose()


def payment_runtime_settings(settings: BotSettings) -> PaymentV2Settings | None:
    if not settings.credit_wallet_enabled:
        return None
    return PaymentV2Settings(
        app_env=settings.app_env,
        database_url=settings.database_url,
        stars_checkout_enabled=settings.stars_checkout_enabled,
        credit_wallet_enabled=settings.credit_wallet_enabled,
        shop_terms_path=settings.shop_terms_path,
    )


async def run_worker(settings: WorkerSettings, inventory_settings: InventorySettings) -> None:
    configure_telegram_logging()
    payment_v2 = payment_runtime_settings(settings)
    cipher = AesGcmInventoryCipher(
        inventory_settings.keyring(),
        inventory_settings.inventory_current_key_version,
        inventory_settings.dedup_key(),
    )
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"timeout": 2},
    )
    try:
        ready = payment_sandbox_ready if payment_v2 is not None else database_ready
        if not await ready(engine):
            raise PollingStartupError("Database unavailable or schema outdated")
        factory = partial(unit_of_work, engine)
        await IdentityService(factory).bootstrap_owner(settings.owner_id)
        await InventoryService(factory, cipher, inventory_settings.low_stock_threshold).check_keys()
        async with create_bot(settings) as bot:
            service = FulfillmentService(
                factory, cipher, FiniteDeliverySender(bot), settings.policy()
            )
            notifications = NotificationService(
                factory,
                TelegramNotificationSender(bot),
                threshold=inventory_settings.low_stock_threshold,
            )
            await run_forever(service, notifications, poll_seconds=settings.worker_poll_seconds)
    finally:
        await engine.dispose()


def create_dispatcher(
    identity: IdentityService,
    access: AccessService,
    catalog: CatalogService,
    inventory: InventoryService,
    pre_checkout: PreCheckoutService,
    payments: PaymentService,
    *,
    resources: ResourceService,
    manual: ManualFulfillmentService,
    history: HistoryService,
    resends: ResendService,
    support: SupportService,
    refunds: RefundService,
    checkout: CheckoutService,
    stars_enabled: bool = True,
    wallet_checkout: WalletCheckoutService | None = None,
    wallet_ledger: WalletLedgerService | None = None,
    topups: TopUpService | None = None,
    qr_link: Callable[[TopUpIntent], str] | None = None,
    bank_enabled: bool = True,
    usdt_enabled: bool = True,
) -> Dispatcher:
    dispatcher = PaymentsDispatcher(payments.record, refunds.record)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_payment_router(payments.record, refunds.record))
    checkout.enabled = stars_enabled
    pre_checkout.enabled = stars_enabled
    dispatcher.include_router(
        create_customer_router(identity, wallet_enabled=wallet_checkout is not None)
    )
    dispatcher.include_router(create_order_router(history, resends, access))
    dispatcher.include_router(create_support_router(support, identity))
    dispatcher.include_router(
        create_catalog_router(catalog, wallet=wallet_checkout, stars_enabled=stars_enabled)
    )
    dispatcher.include_router(create_checkout_router(checkout))
    if wallet_checkout is not None and wallet_ledger is not None and topups is not None:
        dispatcher.include_router(
            create_wallet_router(
                wallet_ledger,
                wallet_checkout,
                topups,
                qr_link,
                bank_enabled=bank_enabled,
                usdt_enabled=usdt_enabled,
            )
        )
        dispatcher.include_router(
            create_wallet_admin_router(
                wallet_ledger,
                wallet_checkout,
                ManualPaymentReviewService(wallet_checkout.uow),
                WalletRefundService(wallet_checkout.uow),
            )
        )
    dispatcher.include_router(
        create_admin_router(access, wallet_enabled=wallet_checkout is not None)
    )
    dispatcher.include_router(create_catalog_admin_router(catalog, access))
    dispatcher.include_router(create_inventory_admin_router(inventory, access))
    dispatcher.include_router(create_resource_admin_router(resources, access))
    dispatcher.include_router(create_manual_admin_router(manual, access))
    dispatcher.include_router(create_refund_admin_router(refunds))
    dispatcher.include_router(create_pre_checkout_router(pre_checkout.check))
    fallback = Router(name="unknown_callback")

    @fallback.callback_query()
    async def unknown_callback(query: CallbackQuery) -> None:
        await query.answer("Nút này không còn hợp lệ. Vui lòng mở lại /start.", show_alert=True)

    dispatcher.include_router(fallback)
    return dispatcher


def runtime_dispatcher(
    factory: UnitOfWorkFactory,
    inventory: InventoryService,
    cipher: AesGcmInventoryCipher,
    bot: Bot,
    public_terms: PublicTerms | None,
    *,
    stars_enabled: bool = True,
    payment_v2: PaymentV2Settings | None = None,
) -> Dispatcher:
    wallet_checkout = wallet_ledger = topups = None
    qr_link = None
    if payment_v2 is not None and payment_v2.credit_wallet_enabled:
        bank = BankAccount(payment_v2.sepay_bank_code, payment_v2.sepay_account_number)
        services = payment_services(factory, payment_v2, public_terms)
        wallet_checkout, wallet_ledger, topups = services.checkout, services.wallet, services.topups

        def qr_link(intent):
            return bank_qr_url(bank, intent.credit_amount, intent.reference)

    return create_dispatcher(
        IdentityService(factory),
        AccessService(factory),
        CatalogService(factory),
        inventory,
        PreCheckoutService(factory),
        PaymentService(factory),
        resources=ResourceService(factory, cipher),
        manual=ManualFulfillmentService(factory, cipher),
        history=HistoryService(factory),
        resends=ResendService(factory, cipher, FiniteDeliverySender(bot)),
        support=SupportService(factory, cipher, public_terms),
        refunds=RefundService(factory, cipher, TelegramRefundGateway(bot)),
        checkout=CheckoutService(factory, StarsInvoiceSender(bot), public_terms),
        stars_enabled=stars_enabled,
        wallet_checkout=wallet_checkout,
        wallet_ledger=wallet_ledger,
        topups=topups,
        qr_link=qr_link,
        bank_enabled=payment_v2 is not None and payment_v2.bank_topup_enabled,
        usdt_enabled=payment_v2 is not None and payment_v2.usdt_topup_enabled,
    )


def payment_services(
    factory: UnitOfWorkFactory, settings: PaymentV2Settings, terms: PublicTerms | None
):
    return OperatorServices(
        IdentityService(factory),
        WalletLedgerService(factory),
        WalletCheckoutService(factory, enabled=settings.credit_wallet_enabled, terms=terms),
        TopUpService(
            factory,
            enabled=settings.credit_wallet_enabled,
            bank=BankAccount(settings.sepay_bank_code, settings.sepay_account_number),
            sandbox_rate=settings.sandbox_usdt_vnd_rate,
            bank_enabled=settings.bank_topup_enabled,
            usdt_enabled=settings.usdt_topup_enabled,
        ),
        ManualPaymentReviewService(factory),
        WalletRefundService(factory),
    )


def create_payment_v2_app(settings: PaymentV2Settings):
    if settings.sepay_webhook_secret is None or not settings.bank_topup_enabled:
        raise InvalidAction("Configure a SePay Sandbox webhook secret and enable bank top-ups")
    engine = create_async_engine(
        settings.database_url.get_secret_value(), hide_parameters=True, connect_args={"timeout": 2}
    )
    factory = partial(unit_of_work, engine)
    services = payment_services(factory, settings, load_terms(settings.shop_terms_path))
    verifier = SePayWebhookVerifier(settings.sepay_webhook_secret.get_secret_value().encode())

    @asynccontextmanager
    async def lifespan(_app):
        try:
            if not await payment_sandbox_ready(engine):
                raise PollingStartupError("Payment sandbox marker/schema unavailable")
            yield
        finally:
            await engine.dispose()

    async def dispatch(raw, headers):
        return await services.topups.ingest(verifier.verify(raw, headers, datetime.now(UTC)))

    app = create_api(partial(payment_sandbox_ready, engine), lifespan=lifespan)
    app.include_router(create_bank_webhook_router(dispatch))
    return app


async def run_payment_command(mode, settings: PaymentV2Settings, argv):
    engine = create_async_engine(
        settings.database_url.get_secret_value(), hide_parameters=True, connect_args={"timeout": 2}
    )
    try:
        if not await payment_sandbox_ready(engine):
            raise InvalidAction("Payment sandbox marker/schema unavailable")
        factory = partial(unit_of_work, engine)
        services = payment_services(factory, settings, load_terms(settings.shop_terms_path))
        if mode == "payments-operator":
            token = (
                None
                if settings.pv2_operator_token is None
                else settings.pv2_operator_token.get_secret_value()
            )
            return await run_operator(argv, settings.pv2_operator_id, token, services)
        parser = argparse.ArgumentParser(prog="telebotshop payments-reconcile")
        source = parser.add_mutually_exclusive_group()
        source.add_argument("--usdt-fixture", type=Path)
        source.add_argument("--binance-readonly", action="store_true")
        parser.add_argument("--since", help="Timezone-aware ISO start for an explicit backfill")
        options = parser.parse_args(argv)
        try:
            since = None if options.since is None else datetime.fromisoformat(options.since)
        except ValueError:
            raise InvalidInput("Invalid reconciliation start") from None
        if options.usdt_fixture is not None:
            reader = SimulatedDepositReader.from_fixture(options.usdt_fixture, Path.cwd())
        elif options.binance_readonly:
            if not settings.binance_readonly_enabled:
                raise InvalidAction("Explicit read-only Binance configuration required")
            assert settings.binance_api_key is not None and settings.binance_api_secret is not None
            assert settings.usdt_network is not None and settings.usdt_token_contract is not None
            reader = BinanceDepositReader(
                settings.binance_api_key.get_secret_value(),
                settings.binance_api_secret.get_secret_value().encode(),
                NetworkPolicy(
                    settings.usdt_network,
                    settings.usdt_token_contract,
                    settings.usdt_minimum_confirmations,
                ),
                account_read_enabled=True,
            )
        else:
            if settings.sepay_sandbox_api_token is None:
                raise InvalidAction("Configure the SePay Sandbox API token for reconciliation")
            reader = SePaySandboxReader(settings.sepay_sandbox_api_token.get_secret_value())
        count = await PaymentReconciliationService(factory, services.topups).run(
            reader, since=since
        )
        print(f"Sandbox observations reconciled: {count}; provider={reader.provider}")
        return 0
    finally:
        await engine.dispose()


def create_webhook_app(settings: WebhookSettings, inventory_settings: InventorySettings) -> FastAPI:
    configure_telegram_logging()
    payment_v2 = payment_runtime_settings(settings)
    public_terms = load_terms(settings.shop_terms_path)
    cipher = AesGcmInventoryCipher(
        inventory_settings.keyring(),
        inventory_settings.inventory_current_key_version,
        inventory_settings.dedup_key(),
    )
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"timeout": 2},
    )
    consumer: Callable[[dict[str, object]], Awaitable[object]] | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal consumer
        try:
            ready = payment_sandbox_ready if payment_v2 is not None else database_ready
            if not await ready(engine):
                raise PollingStartupError("Database unavailable or schema outdated")
            factory = partial(unit_of_work, engine)
            await IdentityService(factory).bootstrap_owner(settings.owner_id)
            inventory = InventoryService(factory, cipher, inventory_settings.low_stock_threshold)
            await inventory.check_keys()
            async with create_bot(settings) as bot:
                dispatcher = runtime_dispatcher(
                    factory,
                    inventory,
                    cipher,
                    bot,
                    public_terms,
                    stars_enabled=settings.stars_checkout_enabled,
                    payment_v2=payment_v2,
                )
                consumer = partial(dispatch_update, partial(dispatcher.feed_update, bot))
                yield
        except (
            SQLAlchemyError,
            TelegramAPIError,
            OSError,
            TimeoutError,
            InventoryCryptoError,
            OwnerConfigurationError,
        ):
            raise PollingStartupError("Webhook startup or transport unavailable") from None
        finally:
            consumer = None
            await engine.dispose()

    async def dispatch(update: dict[str, object]) -> object:
        if consumer is None:
            raise PollingStartupError("Webhook runtime unavailable")
        return await consumer(update)

    app = create_api(partial(database_ready, engine), lifespan=lifespan)
    app.include_router(create_webhook_router(dispatch, settings.webhook_secret.get_secret_value()))
    return app


async def run_polling(settings: BotSettings, inventory_settings: InventorySettings) -> None:
    configure_telegram_logging()
    payment_v2 = payment_runtime_settings(settings)
    public_terms = load_terms(settings.shop_terms_path)
    cipher = AesGcmInventoryCipher(
        inventory_settings.keyring(),
        inventory_settings.inventory_current_key_version,
        inventory_settings.dedup_key(),
    )
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"timeout": 2},
    )
    try:
        ready = payment_sandbox_ready if payment_v2 is not None else database_ready
        if not await ready(engine):
            raise PollingStartupError("Database unavailable or schema outdated")
        factory = partial(unit_of_work, engine)
        await IdentityService(factory).bootstrap_owner(settings.owner_id)
        inventory = InventoryService(factory, cipher, inventory_settings.low_stock_threshold)
        await inventory.check_keys()
        async with create_bot(settings) as bot:
            if (await bot.get_webhook_info()).url:
                raise PollingStartupError("Webhook is configured; polling refused")
            dispatcher = runtime_dispatcher(
                factory,
                inventory,
                cipher,
                bot,
                public_terms,
                stars_enabled=settings.stars_checkout_enabled,
                payment_v2=payment_v2,
            )
            try:
                await dispatcher.start_polling(
                    bot,
                    allowed_updates=["message", "callback_query", "pre_checkout_query"],
                    handle_as_tasks=True,
                    tasks_concurrency_limit=32,
                    close_bot_session=False,
                )
            finally:
                # shortcut: pinned aiogram has no public task drain; replace when available.
                pending = tuple(dispatcher._handle_update_tasks)
                if pending:
                    try:
                        async with asyncio.timeout(10):
                            await asyncio.gather(*pending, return_exceptions=True)
                    except TimeoutError:
                        pass
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(prog="telebotshop")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=(
            "http",
            "polling",
            "webhook",
            "expire-orders",
            "worker",
            "payments-api",
            "payments-operator",
            "payments-reconcile",
        ),
        default="http",
    )
    parsed, remaining = parser.parse_known_args()
    mode = parsed.mode
    if remaining and mode not in {"payments-operator", "payments-reconcile"}:
        parser.error("unrecognized arguments")
    try:
        settings = (
            PaymentV2Settings()
            if mode.startswith("payments-")
            else WorkerSettings()
            if mode == "worker"
            else WebhookSettings()
            if mode == "webhook"
            else BotSettings()
            if mode == "polling"
            else Settings()
        )
        inventory_settings = (
            InventorySettings() if mode in {"polling", "webhook", "worker"} else None
        )
        if isinstance(settings, BotSettings):
            payment_runtime_settings(settings)
    except ValidationError:
        print("Invalid application configuration", file=sys.stderr)
        return 2
    if isinstance(settings, PaymentV2Settings):
        try:
            if mode == "payments-api":
                uvicorn.run(
                    create_payment_v2_app(settings),
                    host=str(settings.http_host),
                    port=settings.http_port,
                    timeout_graceful_shutdown=10,
                )
                return 0
            return asyncio.run(run_payment_command(mode, settings, remaining))
        except (
            InvalidAction,
            InvalidInput,
            AccessDenied,
            SQLAlchemyError,
            OSError,
            TimeoutError,
            OwnerConfigurationError,
            TermsConfigurationError,
        ):
            print(
                "Payment sandbox unavailable; check local setup and operator permissions",
                file=sys.stderr,
            )
            return 1
    if mode == "expire-orders":
        try:
            expired, uncertain = asyncio.run(run_expiry(settings))
        except (ExpiryStartupError, SQLAlchemyError, OSError, TimeoutError):
            print("Order maintenance unavailable", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130
        print(f"Expired orders: {expired}; uncertain invoices: {uncertain}")
        return 0
    if isinstance(settings, WebhookSettings):
        assert inventory_settings is not None
        try:
            app = create_webhook_app(settings, inventory_settings)
        except (TermsConfigurationError, InventoryCryptoError):
            print("Webhook configuration unavailable or incomplete", file=sys.stderr)
            return 2
        uvicorn.run(
            app, host=str(settings.http_host), port=settings.http_port, timeout_graceful_shutdown=10
        )
        return 0
    if isinstance(settings, BotSettings):
        assert inventory_settings is not None
        try:
            if isinstance(settings, WorkerSettings):
                asyncio.run(run_worker(settings, inventory_settings))
            else:
                asyncio.run(run_polling(settings, inventory_settings))
        except OwnerConfigurationError:
            print(
                "Owner configuration mismatch; controlled ownership review required",
                file=sys.stderr,
            )
            return 2
        except PollingStartupError as error:
            print(str(error), file=sys.stderr)
            return 2
        except TermsConfigurationError:
            print("Public terms configuration unavailable or incomplete", file=sys.stderr)
            return 2
        except (SQLAlchemyError, TelegramAPIError, OSError, TimeoutError, InventoryCryptoError):
            print("Bot startup or transport unavailable", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130
        return 0
    uvicorn.run(
        create_app(settings),
        host=str(settings.http_host),
        port=settings.http_port,
        timeout_graceful_shutdown=10,
    )
    return 0
