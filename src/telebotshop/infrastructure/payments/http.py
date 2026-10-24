import json
from decimal import Decimal

import aiohttp

from telebotshop.domain.errors import InvalidAction
from telebotshop.infrastructure.payments.sepay import unique_object


async def get_json(url: str, headers: dict[str, str], query: dict[str, str]):
    if url not in {
        "https://userapi-sandbox.sepay.vn/v2/transactions",
        "https://api.binance.com/sapi/v1/capital/deposit/hisrec",
    }:
        raise InvalidAction("Provider endpoint unavailable")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:
            async with client.get(
                url, headers=headers, params=query, allow_redirects=False
            ) as response:
                if response.status != 200:
                    raise InvalidAction("Settlement provider unavailable")
                body = await response.content.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    raise InvalidAction("Settlement response exceeds bounds")
                return json.loads(body, object_pairs_hook=unique_object, parse_float=Decimal)
    except (aiohttp.ClientError, TimeoutError, ValueError, RecursionError):
        raise InvalidAction("Settlement provider unavailable") from None
