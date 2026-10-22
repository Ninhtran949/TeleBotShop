import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

import uvicorn
from aiogram import Bot, Dispatcher, Router
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
from telebotshop.admin.refunds import create_refund_admin_router
from telebotshop.admin.resources import create_resource_admin_router
from telebotshop.api.health import create_api
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.fulfillment import FulfillmentService
from telebotshop.application.services.history import HistoryService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.inventory import InventoryService
from telebotshop.application.services.manual import ManualFulfillmentService
from telebotshop.application.services.orders import OrderService
from telebotshop.application.services.payments import PaymentService
from telebotshop.application.services.pre_checkout import PreCheckoutService
from telebotshop.application.services.refunds import RefundService
from telebotshop.application.services.resends import ResendService
from telebotshop.application.services.resources import ResourceService
from telebotshop.application.services.support import SupportService
from telebotshop.bot.routers.catalog import create_catalog_router
from telebotshop.bot.routers.identity import create_customer_router
from telebotshop.bot.routers.orders import create_order_router
from telebotshop.bot.routers.support import create_support_router
from telebotshop.domain.errors import OwnerConfigurationError
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.infrastructure.config import (
    BotSettings,
    InventorySettings,
    Settings,
    WorkerSettings,
)
from telebotshop.infrastructure.crypto import AesGcmInventoryCipher
from telebotshop.infrastructure.database.readiness import database_ready
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.delivery.finite import FiniteDeliverySender
from telebotshop.infrastructure.support import TermsConfigurationError, load_terms
from telebotshop.infrastructure.telegram.guard import IngressGuard
from telebotshop.infrastructure.telegram.logging import configure_telegram_logging
from telebotshop.infrastructure.telegram.payments import PaymentsDispatcher, create_payment_router
from telebotshop.infrastructure.telegram.pre_checkout import create_pre_checkout_router
from telebotshop.infrastructure.telegram.refunds import TelegramRefundGateway
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


async def run_worker(settings: WorkerSettings, inventory_settings: InventorySettings) -> None:
    configure_telegram_logging()
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
        if not await database_ready(engine):
            raise PollingStartupError("Database unavailable or schema outdated")
        factory = partial(unit_of_work, engine)
        await IdentityService(factory).bootstrap_owner(settings.owner_id)
        await InventoryService(factory, cipher, inventory_settings.low_stock_threshold).check_keys()
        async with Bot(settings.bot_token.get_secret_value()) as bot:
            service = FulfillmentService(
                factory, cipher, FiniteDeliverySender(bot), settings.policy()
            )
            await run_forever(service, poll_seconds=settings.worker_poll_seconds)
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
) -> Dispatcher:
    dispatcher = PaymentsDispatcher(payments.record, refunds.record)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_payment_router(payments.record, refunds.record))
    dispatcher.include_router(create_customer_router(identity))
    dispatcher.include_router(create_order_router(history, resends, access))
    dispatcher.include_router(create_support_router(support, identity))
    dispatcher.include_router(create_catalog_router(catalog))
    dispatcher.include_router(create_admin_router(access))
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


async def run_polling(settings: BotSettings, inventory_settings: InventorySettings) -> None:
    configure_telegram_logging()
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
        if not await database_ready(engine):
            raise PollingStartupError("Database unavailable or schema outdated")
        factory = partial(unit_of_work, engine)
        identity, access = IdentityService(factory), AccessService(factory)
        await identity.bootstrap_owner(settings.owner_id)
        inventory = InventoryService(factory, cipher, inventory_settings.low_stock_threshold)
        await inventory.check_keys()
        async with Bot(settings.bot_token.get_secret_value()) as bot:
            if (await bot.get_webhook_info()).url:
                raise PollingStartupError("Webhook is configured; polling refused")
            dispatcher = create_dispatcher(
                identity,
                access,
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
        "mode", nargs="?", choices=("http", "polling", "expire-orders", "worker"), default="http"
    )
    mode = parser.parse_args().mode
    try:
        settings = (
            WorkerSettings()
            if mode == "worker"
            else BotSettings()
            if mode == "polling"
            else Settings()
        )
        inventory_settings = InventorySettings() if mode in {"polling", "worker"} else None
    except ValidationError:
        print("Invalid application configuration", file=sys.stderr)
        return 2
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
