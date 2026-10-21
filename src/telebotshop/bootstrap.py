import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

import uvicorn
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from telebotshop.api.health import create_api
from telebotshop.infrastructure.config import Settings
from telebotshop.infrastructure.database.readiness import database_ready


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


def main() -> int:
    try:
        settings = Settings()
    except ValidationError:
        print("Invalid application configuration", file=sys.stderr)
        return 2
    uvicorn.run(
        create_app(settings),
        host=str(settings.http_host),
        port=settings.http_port,
        timeout_graceful_shutdown=10,
    )
    return 0
