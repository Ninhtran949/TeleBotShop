import argparse
import getpass
import hmac
import json
from dataclasses import dataclass
from uuid import UUID

from telebotshop.application.services.identity import IdentityService
from telebotshop.application.services.topup_review import ManualPaymentReviewService
from telebotshop.application.services.topups import TopUpService
from telebotshop.application.services.wallet import WalletLedgerService
from telebotshop.application.services.wallet_checkout import WalletCheckoutService
from telebotshop.application.services.wallet_refunds import WalletRefundService
from telebotshop.domain.errors import AccessDenied, InvalidInput
from telebotshop.domain.models.wallet import WalletPermission


def authenticate_operator(expected: str | None, supplied: str):
    if (
        expected is None
        or not 32 <= len(expected.encode()) <= 256
        or not isinstance(supplied, str)
        or len(supplied) > 256
        or not hmac.compare_digest(expected.encode(), supplied.encode())
    ):
        raise AccessDenied("Operator authentication failed")


@dataclass(frozen=True, repr=False)
class OperatorServices:
    identity: IdentityService
    wallet: WalletLedgerService
    checkout: WalletCheckoutService
    topups: TopUpService
    review: ManualPaymentReviewService
    refunds: WalletRefundService


async def run_operator(
    argv: list[str],
    actor_id: int,
    expected_token: str | None,
    services: OperatorServices,
    *,
    read_token=getpass.getpass,
    confirm=input,
):
    parser = argparse.ArgumentParser(prog="telebotshop payments-operator")
    parser.add_argument(
        "operation",
        choices=[
            "init-owner",
            "status",
            "history",
            "adjust",
            "price",
            "route",
            "reviews",
            "review-detail",
            "review-prepare",
            "review-confirm",
            "refund-prepare",
            "refund-confirm",
            "reverse",
            "grant",
            "cash-refund-record",
            "cash-refund-history",
        ],
    )
    parser.add_argument("--customer", type=int)
    parser.add_argument("--amount", type=int)
    parser.add_argument("--delta", type=int)
    for name in ["action", "product", "event", "intent", "order", "topup"]:
        parser.add_argument("--" + name, type=UUID)
    for name in ["reason", "source", "network", "token-contract", "recipient", "permission"]:
        parser.add_argument("--" + name)
    parser.add_argument("--allow", action="store_true")
    parser.add_argument("--decision", choices=["APPROVE", "REJECT", "ESCALATE"], default="APPROVE")
    parser.add_argument("--state", choices=["REQUESTED", "UNCERTAIN", "CONFIRMED", "REJECTED"])
    args = parser.parse_args(argv)
    if expected_token is None:
        raise AccessDenied("Configure the local sandbox operator token first")
    authenticate_operator(expected_token, read_token("Sandbox operator token (hidden): "))

    def require(*names):
        if any(getattr(args, name) is None for name in names):
            raise InvalidInput("Missing required operator arguments")

    op = args.operation
    if op in {"init-owner", "adjust", "price", "route", "reverse", "grant", "cash-refund-record"}:
        print("SANDBOX operation:", op)
        if confirm("Type CONFIRM SANDBOX to apply: ") != "CONFIRM SANDBOX":
            raise AccessDenied("Operator confirmation declined")
    if op == "init-owner":
        await services.identity.bootstrap_owner(actor_id)
        output = {"sandbox_owner_initialized": True}
    elif op == "status":
        result = await services.wallet.reconcile(actor_id)
        output = {"balanced": result.balanced, "mismatched_accounts": result.mismatched_accounts}
    elif op == "history":
        require("customer")
        balance, entries = await services.wallet.operator_history(actor_id, args.customer)
        output = {
            "available": balance.available,
            "entries": [
                {"id": str(e.id), "kind": e.kind, "delta": e.delta, "at": e.created_at.isoformat()}
                for e in entries
            ],
        }
    elif op == "adjust":
        require("customer", "delta", "action", "reason", "source")
        entry = await services.wallet.adjust(
            actor_id, args.customer, args.delta, args.action, args.reason, args.source
        )
        output = {"journal_id": str(entry.id), "delta": entry.delta}
    elif op == "price":
        require("product", "amount", "action", "reason")
        await services.checkout.set_price(
            actor_id, args.product, args.amount, args.action, args.reason
        )
        output = {"credit_price": args.amount, "product_id": str(args.product)}
    elif op == "route":
        require("customer", "network", "token_contract", "recipient")
        await services.topups.set_sandbox_route(
            actor_id, args.customer, args.network, args.token_contract, args.recipient
        )
        output = {"sandbox_route_configured": True}
    elif op == "reviews":
        rows = await services.review.queue(actor_id)
        output = [
            {
                "event_id": str(e.event_id),
                "intent_id": str(e.intent_id),
                "state": e.state,
                "reason": e.reason,
            }
            for e in rows
        ]
    elif op == "review-detail":
        require("event")
        event, result, intent = await services.review.detail(actor_id, args.event)
        output = {
            "event_id": str(result.event_id),
            "state": result.state,
            "provider": event.provider,
            "txid": event.transaction_id,
            "received_amount": str(event.amount),
            "asset": event.asset,
            "recipient": event.recipient,
            "network": event.network,
            "token_verified": event.token_verified,
            "confirmations": event.confirmations,
            "intent_id": None if intent is None else str(intent.id),
            "requested_credits": None if intent is None else intent.credit_amount,
            "reason": result.reason,
        }
    elif op == "review-prepare":
        require("event", "action", "reason", "source")
        await services.review.prepare(
            actor_id,
            args.event,
            args.intent,
            args.action,
            args.reason,
            args.source,
            decision=args.decision,
        )
        output = {"confirmation_action": str(args.action)}
    elif op == "review-confirm":
        require("action")
        result = await services.review.confirm(actor_id, args.action)
        output = {"state": result.state, "intent_id": str(result.intent_id)}
    elif op == "refund-prepare":
        require("order", "action", "reason", "source")
        await services.refunds.prepare(actor_id, args.order, args.action, args.reason, args.source)
        output = {"confirmation_action": str(args.action)}
    elif op == "refund-confirm":
        require("action")
        entry = await services.refunds.confirm(actor_id, args.action)
        output = {"refund_journal_id": str(entry.id)}
    elif op == "reverse":
        require("topup", "reason", "source")
        entry = await services.refunds.reverse_topup(actor_id, args.topup, args.reason, args.source)
        output = {"reversal_journal_id": str(entry.id), "external_money_refunded": False}
    elif op == "cash-refund-record":
        require("topup", "action", "state", "reason", "source")
        await services.refunds.record_external_refund(
            actor_id, args.topup, args.action, args.state, args.reason, args.source
        )
        output = {"recorded_state": args.state, "money_moved_by_app": False}
    elif op == "cash-refund-history":
        require("topup")
        rows = await services.refunds.external_refund_history(actor_id, args.topup)
        output = [
            {"action": str(r["id"]), "state": r["state"], "at": r["created_at"].isoformat()}
            for r in rows
        ]
    else:
        require("customer", "permission", "reason")
        try:
            permission = WalletPermission(args.permission)
        except ValueError:
            raise InvalidInput("Invalid wallet permission") from None
        await services.wallet.grant_permission(
            actor_id, args.customer, permission, args.allow, args.reason
        )
        output = {"delegation_applied": True}
    print(json.dumps(output, ensure_ascii=False))
    return 0
