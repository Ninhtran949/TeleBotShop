import asyncio
import json
import time
from datetime import timedelta, timezone

from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.topups import SettlementSource
from telebotshop.infrastructure.payments.http import get_json
from telebotshop.infrastructure.payments.sepay import parse_bank_event


class SePaySandboxReader:
    provider = "SEPAY"

    def __init__(self, token: str, *, request=get_json):
        self.token, self.request = token, request
        self._lock = asyncio.Lock()
        self._next_request = 0.0

    async def read(self, after, before, page):
        zone = timezone(timedelta(hours=7))
        query = {
            "transaction_date_from": after.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S"),
            "transaction_date_to": before.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp_format": "iso8601",
            "per_page": "100",
            "page": str(page),
            "transaction_date_sort": "asc",
        }
        # SePay limits this endpoint to three requests per second per IP.
        async with self._lock:
            await asyncio.sleep(max(0, self._next_request - time.monotonic()))
            self._next_request = time.monotonic() + 0.34
            payload = await self.request(
                "https://userapi-sandbox.sepay.vn/v2/transactions",
                {"Authorization": "Bearer " + self.token},
                query,
            )
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise InvalidAction("Invalid sandbox settlement response")
        rows = payload.get("data")
        if not isinstance(rows, list) or len(rows) > 100:
            raise InvalidAction("Invalid sandbox settlement response")
        results = []
        try:
            for row in rows:
                value = {
                    "gateway": row["bank_brand_name"],
                    "accountNumber": row["account_number"],
                    "referenceCode": row["reference_number"],
                    "transactionDate": row["transaction_date"],
                    "transferType": row["transfer_type"],
                    "transferAmount": row["amount_in"]
                    if row["transfer_type"] == "in"
                    else row["amount_out"],
                    "content": row["transaction_content"],
                    "code": row.get("code"),
                }
                results.append(
                    parse_bank_event(
                        value, json.dumps(row, default=str).encode(), SettlementSource.SEPAY_API
                    )
                )
        except (TypeError, KeyError):
            raise InvalidAction("Invalid sandbox settlement response") from None
        return tuple(results)
