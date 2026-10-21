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
from telebotshop.api.health import create_api
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.catalog import CatalogService
from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.inventory import InventoryService
from telebotshop.application.services.orders import OrderService
from telebotshop.bot.routers.catalog import create_catalog_router
from telebotshop.bot.routers.identity import create_customer_router
from telebotshop.domain.errors import OwnerConfigurationError
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.domain.models.pre_checkout import PreCheckout
from telebotshop.infrastructure.config import BotSettings, InventorySettings, Settings
from telebotshop.infrastructure.crypto import AesGcmInventoryCipher
from telebotshop.infrastructure.database.readiness import database_ready
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.telegram.guard import IngressGuard
from telebotshop.infrastructure.telegram.logging import configure_telegram_logging
from telebotshop.infrastructure.telegram.pre_checkout import create_pre_checkout_router


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


async def _pre_checkout_unavailable(_request: PreCheckout) -> bool:
    # Durable receipt intake is required before enabling approvals in T08.
    return False


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


def create_dispatcher(
    identity: IdentityService,
    access: AccessService,
    catalog: CatalogService,
    inventory: InventoryService,
) -> Dispatcher:
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_customer_router(identity))
    dispatcher.include_router(create_catalog_router(catalog))
    dispatcher.include_router(create_admin_router(access))
    dispatcher.include_router(create_catalog_admin_router(catalog, access))
    dispatcher.include_router(create_inventory_admin_router(inventory, access))
    dispatcher.include_router(create_pre_checkout_router(_pre_checkout_unavailable))
    fallback = Router(name="unknown_callback")

    @fallback.callback_query()
    async def unknown_callback(query: CallbackQuery) -> None:
        await query.answer("Nút này không còn hợp lệ. Vui lòng mở lại /start.", show_alert=True)

    dispatcher.include_router(fallback)
    return dispatcher


async def run_polling(settings: BotSettings, inventory_settings: InventorySettings) -> None:
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
        identity, access = IdentityService(factory), AccessService(factory)
        await identity.bootstrap_owner(settings.owner_id)
        inventory = InventoryService(factory, cipher, inventory_settings.low_stock_threshold)
        await inventory.check_keys()
        async with Bot(settings.bot_token.get_secret_value()) as bot:
            if (await bot.get_webhook_info()).url:
                raise PollingStartupError("Webhook is configured; polling refused")
            dispatcher = create_dispatcher(identity, access, CatalogService(factory), inventory)
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
        "mode", nargs="?", choices=("http", "polling", "expire-orders"), default="http"
    )
    mode = parser.parse_args().mode
    try:
        settings = BotSettings() if mode == "polling" else Settings()
        inventory_settings = InventorySettings() if mode == "polling" else None
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
