"""Run explicitly: python -m tests.e2e preflight | login --actor owner."""

import argparse
import asyncio
import importlib.metadata
import json
import logging
import subprocess
import sys
from pathlib import Path

from tests.e2e.login import interactive_login, owner_phone, safe_error
from tests.e2e.safety import FIXTURES, E2EBlocked, load_config


async def run(args):
    # Import the optional SDK only when explicitly running external acceptance.
    from tests.e2e.client import require_actor, target, user_client

    config = load_config(Path(__file__).resolve().parents[2])
    async with target(config) as (engine, bot, me):
        if args.command == "preflight":
            from sqlalchemy import text

            from telebotshop.infrastructure.database.schema import SCHEMA_HEAD
            from tests.e2e.evidence import Evidence

            async with engine.connect() as connection:
                counts = {
                    table: await connection.scalar(text(f"SELECT count(*) FROM {table}"))
                    for table in ("products", "orders", "payments", "fulfillments")
                }
                overdue = await connection.scalar(
                    text(
                        "SELECT count(*) FROM orders WHERE status='AWAITING_PAYMENT' "
                        "AND expires_at<clock_timestamp()"
                    )
                )
            record = {
                "preflight": "PASS",
                "server": "test",
                "bot": me.username,
                "counts": counts,
                "overdue_unpaid": overdue,
                "git_head": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=config.root, text=True
                ).strip(),
                "source_sha256": Evidence(config.root, args.actor).source_sha256,
                "schema_head": SCHEMA_HEAD,
                "transport": "polling",
                "operator": args.actor,
                "sdk": {name: importlib.metadata.version(name) for name in ("aiogram", "telethon")},
            }
            output = config.root / ".cache/t14-e2e/preflight.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(record, indent=2) + "\n", "utf-8")
            print(json.dumps(record))
            return
        if args.command == "expire":
            from telebotshop.bootstrap import run_expiry

            expired, recovered = await run_expiry(config.bot)
            print(json.dumps({"expired_unpaid": expired, "recovered_invoices": recovered}))
            return
        if args.command == "support-pair":
            from tests.e2e.operations import support_pair

            async with user_client(config, "owner") as owner_client:
                owner = await require_actor(owner_client, config, "owner")
                async with user_client(config, "buyer2") as buyer_client:
                    buyer = await require_actor(buyer_client, config, "buyer2")
                    await support_pair(config, owner_client, buyer_client, me, owner, buyer)
            return
        async with user_client(config, args.actor, login=args.command == "login") as client:
            if args.command == "login" and not await client.is_user_authorized():
                if not sys.stdin.isatty():
                    raise E2EBlocked("Login requires an interactive Owner terminal")
                configured_phone = config.owner_phone if args.actor == "owner" else None
                phone = (
                    configured_phone.get_secret_value()
                    if configured_phone is not None
                    else await owner_phone()
                )
                await interactive_login(client, phone)
            user = await require_actor(client, config, args.actor)
            if args.command == "login":
                print(json.dumps({"login": "PASS", "actor": args.actor, "server": "test"}))
            elif args.command == "navigation":
                from tests.e2e.navigation import navigation

                await navigation(config, args.actor, client, me, user)
            elif args.command == "purchase":
                from tests.e2e.checkout import purchase
                from tests.e2e.delivery import verify_delivery

                driver, evidence, order = await purchase(
                    config, args.actor, client, me, user, engine, args.sku
                )
                await verify_delivery(client, driver, evidence, engine, user, args.sku, order)
            elif args.command in {"support", "refund"}:
                from tests.e2e.operations import refund, support

                if args.command == "support":
                    await support(config, args.actor, client, me, user)
                else:
                    await refund(config, args.actor, client, me, user, engine, args.sku)


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in isolated Telegram acceptance")
    parser.add_argument(
        "command",
        choices=(
            "preflight",
            "login",
            "navigation",
            "expire",
            "purchase",
            "support",
            "support-pair",
            "refund",
        ),
    )
    parser.add_argument("--actor", choices=("owner", "buyer2"), default="owner")
    parser.add_argument("--sku", choices=tuple(FIXTURES))
    args = parser.parse_args()
    if args.command in {"purchase", "refund"} and args.sku is None:
        parser.error("purchase/refund requires an explicit --sku")
    if args.command == "support-pair" and args.actor != "owner":
        parser.error("support-pair requires --actor owner")
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(run(args))
        return 0
    except E2EBlocked as error:
        print(f"BLOCKED: {error}")
    except Exception as error:
        # External errors can embed phone/OTP/token/payload; expose only their type.
        record = safe_error(error)
        output = Path(__file__).resolve().parents[2] / ".cache/t14-e2e/last-error.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2) + "\n", "utf-8")
        print(
            f"BLOCKED: {record['error_type']}; request={record['request_type']}; "
            "inspect the local setup, do not retry effects"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
