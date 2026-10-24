import asyncio
import hmac
import json
import logging
import re
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from telebotshop.api.health import HEALTH_HEADERS
from telebotshop.domain.errors import InvalidInput

logger = logging.getLogger(__name__)
BODY_LIMIT = 256 * 1024


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON field")
    return result


def create_webhook_router(
    dispatch: Callable[[dict[str, object]], Awaitable[object]], secret: str
) -> APIRouter:
    if not secret:
        raise ValueError("Webhook secret required")
    router = APIRouter()
    slots = asyncio.Semaphore(32)

    def response(status: int) -> JSONResponse:
        return JSONResponse(
            {"status": "ok" if status == 200 else "unavailable"},
            status_code=status,
            headers=HEALTH_HEADERS,
        )

    @router.post("/telegram/webhook")
    async def webhook(request: Request) -> JSONResponse:
        headers = request.headers.getlist("x-telegram-bot-api-secret-token")
        if len(headers) != 1 or not hmac.compare_digest(headers[0].encode(), secret.encode()):
            return response(403)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json"
            or request.headers.get("content-encoding", "identity").lower() != "identity"
        ):
            return response(415)
        length = request.headers.get("content-length")
        if length is not None:
            if re.fullmatch("[0-9]{1,10}", length) is None:
                return response(400)
            if int(length) > BODY_LIMIT:
                return response(413)
        if slots.locked():
            return response(503)
        async with slots:
            try:
                body = bytearray()
                async with asyncio.timeout(2):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > BODY_LIMIT:
                            return response(413)
                        body.extend(chunk)
                try:
                    payload = json.loads(body, object_pairs_hook=unique_object)
                    if not isinstance(payload, dict):
                        return response(400)
                except (ValueError, RecursionError):
                    return response(400)
                async with asyncio.timeout(20):
                    await dispatch(payload)
            except InvalidInput:
                return response(400)
            except Exception:
                # Raw SDK/database/validation errors can contain payment data or credentials.
                logger.warning("telegram_webhook_processing_failed")
                return response(503)
        return response(200)

    return router
