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

from telebotshop.admin.identity import create_admin_router
from telebotshop.api.health import create_api
from telebotshop.application.services.access import AccessService
from telebotshop.application.services.identity import IdentityService
from telebotshop.bot.routers.identity import create_customer_router
from telebotshop.domain.errors import OwnerConfigurationError
from telebotshop.infrastructure.config import BotSettings, Settings
from telebotshop.infrastructure.database.readiness import database_ready
from telebotshop.infrastructure.database.uow import unit_of_work
from telebotshop.infrastructure.telegram.guard import IngressGuard
from telebotshop.infrastructure.telegram.logging import configure_telegram_logging


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


def create_dispatcher(identity: IdentityService, access: AccessService) -> Dispatcher:
    dispatcher = Dispatcher(disable_fsm=True)
    guard = IngressGuard()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(create_customer_router(identity))
    dispatcher.include_router(create_admin_router(access))
    fallback = Router(name="unknown_callback")

    @fallback.callback_query()
    async def unknown_callback(query: CallbackQuery) -> None:
        await query.answer("Nút này không còn hợp lệ. Vui lòng mở lại /start.", show_alert=True)

    dispatcher.include_router(fallback)
    return dispatcher


async def run_polling(settings: BotSettings) -> None:
    configure_telegram_logging()
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
        async with Bot(settings.bot_token.get_secret_value()) as bot:
            if (await bot.get_webhook_info()).url:
                raise PollingStartupError("Webhook is configured; polling refused")
            await create_dispatcher(identity, access).start_polling(
                bot,
                allowed_updates=["message", "callback_query"],
                handle_as_tasks=False,
                close_bot_session=False,
            )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(prog="telebotshop")
    parser.add_argument("mode", nargs="?", choices=("http", "polling"), default="http")
    mode = parser.parse_args().mode
    try:
        settings = BotSettings() if mode == "polling" else Settings()
    except ValidationError:
        print("Invalid application configuration", file=sys.stderr)
        return 2
    if isinstance(settings, BotSettings):
        try:
            asyncio.run(run_polling(settings))
        except OwnerConfigurationError:
            print(
                "Owner configuration mismatch; controlled ownership review required",
                file=sys.stderr,
            )
            return 2
        except PollingStartupError as error:
            print(str(error), file=sys.stderr)
            return 2
        except (SQLAlchemyError, TelegramAPIError, OSError, TimeoutError):
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
