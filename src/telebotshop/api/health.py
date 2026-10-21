from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

HEALTH_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def create_api(
    ready: Callable[[], Awaitable[bool]],
    *,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]],
) -> FastAPI:
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health/live")
    async def live() -> JSONResponse:
        return JSONResponse({"status": "ok"}, headers=HEALTH_HEADERS)

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        available = await ready()
        return JSONResponse(
            {"status": "ok" if available else "unavailable"},
            status_code=200 if available else 503,
            headers=HEALTH_HEADERS,
        )

    return app
