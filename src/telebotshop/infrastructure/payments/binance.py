import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from telebotshop.domain.errors import InvalidAction, InvalidInput
from telebotshop.domain.models.payments import validate_receipt_text
from telebotshop.domain.models.topups import Settlement, SettlementSource
from telebotshop.infrastructure.payments.http import get_json


@dataclass(frozen=True, repr=False)
class NetworkPolicy:
    network: str
    token_contract: str
    minimum_confirmations: int

    def __post_init__(self):
        for value in [self.network, self.token_contract]:
            validate_receipt_text(value, 200)
        if (
            type(self.minimum_confirmations) is not int
            or not 1 <= self.minimum_confirmations <= 1000000
        ):
            raise InvalidInput("Invalid finality policy")


def deposit_evidence(row, policy: NetworkPolicy, *, simulated=False):
    try:
        network = row["network"]
        txid = row["txId"]
        if re.fullmatch(r"(?:0x)?[0-9a-fA-F]{64}", txid):
            txid = txid.lower()
        progress = re.fullmatch(r"([0-9]{1,7})/([0-9]{1,7})", row["confirmTimes"])
        if progress is None:
            raise ValueError
        confirmed, required = map(int, progress.groups())
        token = (
            (row.get("sandbox_token_contract", "") if simulated else policy.token_contract)
            if network == policy.network
            else ""
        )
        if type(row["amount"]) is not str:
            raise ValueError
        # Exchange history does not attest the token contract or a buyer-owned deposit route.
        # shortcut: independent chain proof is not wired; actual exchange evidence stays pending.
        token_verified = simulated and row.get("sandbox_token_contract") == policy.token_contract
        return Settlement(
            "BINANCE",
            network + ":" + txid,
            None,
            row["address"],
            row["coin"],
            Decimal(row["amount"]),
            network,
            token,
            type(row["status"]) is int and row["status"] == 1 and row.get("travelRuleStatus") == 0,
            confirmed,
            max(required, policy.minimum_confirmations),
            token_verified,
            "in",
            datetime.fromtimestamp(row["insertTime"] / 1000, UTC),
            hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).digest(),
            SettlementSource.SIMULATED if simulated else SettlementSource.BINANCE_API,
        )
    except (ValueError, TypeError, KeyError, OverflowError, InvalidOperation):
        raise InvalidInput("Invalid deposit evidence") from None


class BinanceDepositReader:
    provider = "BINANCE"

    def __init__(
        self,
        key: str,
        secret: bytes,
        policy: NetworkPolicy,
        *,
        account_read_enabled=False,
        request=get_json,
    ):
        self.key, self.secret, self.policy = key, secret, policy
        self.enabled, self.request = account_read_enabled, request

    async def read(self, after, before, page):
        if self.enabled is not True:
            raise InvalidAction("Binance account access requires explicit configuration")
        query = {
            "coin": "USDT",
            "startTime": str(int(after.timestamp() * 1000)),
            "endTime": str(int(before.timestamp() * 1000)),
            "limit": "100",
            "offset": str((page - 1) * 100),
            "recvWindow": "5000",
            "timestamp": str(int(datetime.now(UTC).timestamp() * 1000)),
        }
        query["signature"] = hmac.new(
            self.secret, urlencode(query).encode(), hashlib.sha256
        ).hexdigest()
        rows = await self.request(
            "https://api.binance.com/sapi/v1/capital/deposit/hisrec",
            {"X-MBX-APIKEY": self.key},
            query,
        )
        if not isinstance(rows, list) or len(rows) > 100:
            raise InvalidAction("Invalid deposit history response")
        return tuple(deposit_evidence(row, self.policy) for row in rows)


class SimulatedDepositReader:
    provider = "BINANCE"

    def __init__(self, rows, policy: NetworkPolicy):
        self.rows, self.policy = tuple(rows), policy

    @classmethod
    def from_fixture(cls, path: Path, workspace: Path):
        resolved = path.resolve()
        if (
            not resolved.is_relative_to((workspace / ".cache").resolve())
            or resolved.suffix != ".json"
        ):
            raise InvalidInput("Sandbox fixtures must be JSON under the workspace cache")
        try:
            with resolved.open("rb") as stream:
                body = stream.read(65537)
            if len(body) > 65536:
                raise ValueError
            value = json.loads(body)
            if (
                value["source"] != "SIMULATED_BINANCE"
                or not isinstance(value["deposits"], list)
                or len(value["deposits"]) > 99
            ):
                raise ValueError
            policy = NetworkPolicy(
                value["network"], value["token_contract"], value["minimum_confirmations"]
            )
            if (
                type(policy.minimum_confirmations) is not int
                or not 1 <= policy.minimum_confirmations <= 1000000
            ):
                raise ValueError
            return cls(value["deposits"], policy)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise InvalidInput("Invalid simulated deposit fixture") from None

    async def read(self, after, before, page):
        if page != 1:
            return ()
        events = tuple(deposit_evidence(row, self.policy, simulated=True) for row in self.rows)
        return tuple(event for event in events if after <= event.occurred_at <= before)
