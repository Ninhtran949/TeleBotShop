"""Fail closed before accessing the isolated Telegram acceptance environment."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import SecretStr, ValidationError
from sqlalchemy.engine import make_url

from telebotshop.infrastructure.config import BotSettings

TEST_DCS = {1: "149.154.175.40", 2: "149.154.167.40", 3: "149.154.175.117"}
DATABASE_MARKER = "telebotshop:telegram-test:v1"
FIXTURES = {
    "T14_KEY": "UNIQUE_KEY",
    "T14_LICENSE": "LICENSE",
    "T14_FILE": "DIGITAL_FILE",
    "T14_TEXT": "REUSABLE_RESOURCE",
    "T14_DOC": "REUSABLE_RESOURCE",
    "T14_MANUAL": "MANUAL_FULFILLMENT",
    "T14_RACE": "UNIQUE_KEY",
}


class E2EBlocked(RuntimeError):
    """An acceptance precondition failed; messages must contain no sensitive inputs."""


@dataclass(frozen=True, repr=False)
class E2EConfig:
    root: Path
    bot: BotSettings
    api_id: int
    api_hash: SecretStr
    owner_phone: SecretStr | None = None


def normalize_phone(value: str) -> str:
    phone = re.sub(r"[()\s-]", "", value)
    if not re.fullmatch(r"\+[1-9][0-9]{1,14}", phone):
        raise E2EBlocked("Test phone must use +country code and number; check it locally")
    return phone


def load_config(root: Path) -> E2EConfig:
    root = root.resolve()
    values = dotenv_values(root / ".env.telegram-test", interpolate=False)
    required = {"APP_ENV", "TELEGRAM_ENVIRONMENT", "DATABASE_URL", "BOT_TOKEN", "OWNER_ID"}
    if not all(values.get(key) for key in required):
        raise E2EBlocked("Isolated bot configuration is incomplete")
    try:
        bot = BotSettings(
            **{k.lower(): v for k, v in values.items() if k.lower() in BotSettings.model_fields}
        )
    except ValidationError:
        raise E2EBlocked("Invalid isolated bot configuration") from None
    target = make_url(bot.database_url.get_secret_value())
    if (
        bot.app_env != "test"
        or bot.telegram_environment != "test"
        or (target.host, target.port, target.database)
        != ("127.0.0.1", 55432, "telebotshop_telegram_test")
        or bot.shop_terms_path is None
        or bot.shop_terms_path.resolve() != root / ".cache/telegram-test/terms-test.md"
    ):
        raise E2EBlocked("Only the pinned Test Server/database/terms target is allowed")
    main = dotenv_values(root / ".env", interpolate=False).get("BOT_TOKEN")
    if main and bot.bot_token.get_secret_value() == main:
        raise E2EBlocked("The main bot credential cannot be used for acceptance")
    api = dotenv_values(root / ".env.telegram-e2e", interpolate=False)
    api_id, api_hash = api.get("T14_API_ID") or "", api.get("T14_API_HASH") or ""
    if not re.fullmatch(r"[1-9][0-9]{0,9}", api_id) or not re.fullmatch(
        r"[0-9a-fA-F]{32}", api_hash
    ):
        raise E2EBlocked("Fill T14_API_ID/T14_API_HASH in .env.telegram-e2e locally")
    phone = values.get("Phone_Test_Server")
    return E2EConfig(
        root,
        bot,
        int(api_id),
        SecretStr(api_hash),
        None if phone is None else SecretStr(normalize_phone(phone)),
    )


def session_path(root: Path, actor: str) -> Path:
    if actor not in {"owner", "buyer2"}:
        raise E2EBlocked("Unknown test actor")
    directory = root.resolve() / ".cache/telegram-test/mtproto"
    if directory.resolve() != directory:
        raise E2EBlocked("The private session directory must not use symbolic links")
    path = directory / f"{actor}.session"
    if path.is_symlink():
        raise E2EBlocked("The private session must not use a symbolic link")
    return path


def require_test_session(session) -> None:
    if TEST_DCS.get(session.dc_id) != session.server_address or session.port not in {80, 443}:
        raise E2EBlocked("Refusing a non-Test-Server session before connection")


def validate_invoice(media, form, bot_id: int) -> None:
    if (
        media.test is not True
        or media.currency != "XTR"
        or type(media.total_amount) is not int
        or media.total_amount != 1
        or form.bot_id != bot_id
        or form.invoice.test is not True
        or form.invoice.currency != "XTR"
        or len(form.invoice.prices) != 1
        or any(type(price.amount) is not int for price in form.invoice.prices)
        or sum(price.amount for price in form.invoice.prices) != 1
    ):
        raise E2EBlocked("Only a one-Star Test invoice from the verified bot may be paid")


def validate_topup(form, option) -> None:
    try:
        params = json.loads(form.native_params.data)
        provider = urlsplit(form.url)
    except (AttributeError, TypeError, ValueError):
        raise E2EBlocked("The Test top-up form is incomplete") from None
    if (
        type(option.stars) is not int
        or option.stars != 15
        or type(option.amount) is not int
        or option.amount <= 0
        or form.invoice.test is not True
        or form.invoice.currency != option.currency
        or any(type(price.amount) is not int for price in form.invoice.prices)
        or sum(price.amount for price in form.invoice.prices) != option.amount
        or form.native_provider != "stripe"
        or not isinstance(params, dict)
        or not str(params.get("publishable_key", "")).startswith("pk_test_")
        or (provider.scheme, provider.hostname) != ("https", "i-bots.stel.com")
        or provider.username is not None
        or provider.password is not None
        or any(
            getattr(form.invoice, field, False)
            for field in (
                "name_requested",
                "phone_requested",
                "email_requested",
                "shipping_address_requested",
            )
        )
    ):
        raise E2EBlocked("Only the verified 15-Star Stripe TEST top-up may be submitted")


def validate_actor(user, owner_id: int, actor: str) -> None:
    if (
        actor not in {"owner", "buyer2"}
        or user is None
        or user.bot
        or (user.id == owner_id) != (actor == "owner")
    ):
        raise E2EBlocked("The Test session belongs to the wrong acceptance actor")
