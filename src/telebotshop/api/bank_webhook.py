import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from telebotshop.api.health import HEALTH_HEADERS
from telebotshop.domain.errors import InvalidInput


def create_bank_webhook_router(dispatch: Callable[[bytes, dict[str, str]], Awaitable[object]]):
    router = APIRouter()
    slots = asyncio.Semaphore(16)

    @router.post("/payments/sepay/webhook")
    async def webhook(request: Request):
        def response(status):
            return JSONResponse(
                {"success": status == 200}, status_code=status, headers=HEALTH_HEADERS
            )

        names = ["x-sepay-timestamp", "x-sepay-signature"]
        if any(len(request.headers.getlist(name)) != 1 for name in names):
            return response(401)
        if (
            request.headers.get("content-type", "").split(";")[0].lower() != "application/json"
            or request.headers.get("content-encoding", "identity") != "identity"
        ):
            return response(415)
        if slots.locked():
            return response(503)
        async with slots:
            try:
                body = bytearray()
                async with asyncio.timeout(2):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > 65536:
                            return response(413)
                        body.extend(chunk)
                async with asyncio.timeout(15):
                    await dispatch(bytes(body), {name: request.headers[name] for name in names})
            except InvalidInput:
                return response(401)
            except Exception:
                return response(503)
        return response(200)

    return router
