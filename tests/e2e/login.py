"""Interactive Owner input for the Test user client."""

import asyncio
import getpass
import re
import traceback
from pathlib import Path

from tests.e2e.safety import E2EBlocked, normalize_phone

SAFE_RPC_CODES = frozenset(
    {
        "BALANCE_TOO_LOW",
        "BOT_INVOICE_INVALID",
        "FORM_EXPIRED",
        "FORM_ID_EMPTY",
        "FORM_SUBMIT_DUPLICATE",
        "FORM_UNSUPPORTED",
        "PRECHECKOUT_FAILED",
        "STARS_FORM_AMOUNT_MISMATCH",
        "CONNECTION_NOT_INITED",
    }
)


async def owner_input(prompt: str, *, hidden: bool = False) -> str:
    return (await asyncio.to_thread(getpass.getpass if hidden else input, prompt)).strip()


async def owner_phone() -> str:
    while True:
        value = await owner_input("Test Server phone with +country code (hidden): ", hidden=True)
        try:
            return normalize_phone(value)
        except E2EBlocked:
            print("Invalid phone. Enter +country code and number locally; no code was requested.")


async def interactive_login(client, phone: str) -> None:
    from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError

    async with asyncio.timeout(30):
        await client.initialize_test_connection()
        sent = await client.send_code_request(phone)
    delivery = {
        "SentCodeTypeApp": "Telegram service chat in your Test Server account",
        "SentCodeTypeSms": "SMS",
        "SentCodeTypeCall": "phone call",
    }.get(type(sent.type).__name__, "the method chosen by Telegram")
    print(f"Login code delivery: {delivery}. Enter the code only in this terminal.")
    for attempt in range(3):
        code = await owner_input("Test Server login code (hidden): ", hidden=True)
        if not code:
            print("Empty code. Enter the latest Test Server code; no new code was requested.")
            continue
        try:
            async with asyncio.timeout(30):
                await client.initialize_test_connection()
                await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
            return
        except PhoneCodeInvalidError:
            if attempt == 2:
                raise
            print("Code rejected. Re-enter the latest Test Server code; no new code was requested.")
        except SessionPasswordNeededError:
            password = await owner_input("Test account 2FA (hidden): ", hidden=True)
            async with asyncio.timeout(30):
                await client.initialize_test_connection()
                await client.sign_in(password=password)
            return
    raise E2EBlocked("No code entered after three prompts; stopped without requesting another code")


def safe_error(error: Exception) -> dict:
    # RPC error text/request fields can contain phone, API hash or payment data.
    result = {
        "error_type": type(error).__name__,
        "request_type": type(getattr(error, "request", None)).__name__,
        "trace": [
            {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
            for frame in traceback.extract_tb(error.__traceback__)
        ],
    }
    seconds = getattr(error, "seconds", None)
    if type(seconds) is int:
        result["wait_seconds"] = seconds
    code = getattr(error, "code", None)
    if type(code) is int:
        result["rpc_status"] = code
    identifier = getattr(error, "message", None)
    sdk_identifier = (
        type(error).__module__.startswith("telethon.errors.")
        and isinstance(identifier, str)
        and len(identifier) <= 64
        and re.fullmatch(r"[A-Z]+(?:_[A-Z]+)+", identifier) is not None
    )
    if isinstance(identifier, str) and (identifier in SAFE_RPC_CODES or sdk_identifier):
        result["rpc_error"] = identifier
    return result
