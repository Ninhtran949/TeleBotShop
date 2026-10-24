import asyncio
import json
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.e2e import login
from tests.e2e.login import owner_input, safe_error
from tests.e2e.safety import E2EBlocked


@pytest.mark.asyncio
@pytest.mark.parametrize("hidden", [False, True])
async def test_owner_input_keeps_the_telegram_network_loop_running(monkeypatch, hidden):
    heartbeat = threading.Event()

    def entered(prompt):
        assert heartbeat.wait(0.5), "Owner input blocked the Telegram network loop"
        return "  FAKE local input  "

    async def network():
        await asyncio.sleep(0.01)
        heartbeat.set()

    monkeypatch.setattr("getpass.getpass" if hidden else "builtins.input", entered)
    task = asyncio.create_task(network())
    try:
        assert await owner_input("FAKE prompt", hidden=hidden) == "FAKE local input"
    finally:
        await task


def test_login_diagnostics_never_serialize_error_text_or_request_fields():
    error = RuntimeError("FAKE private phone/code/session")
    error.request = SimpleNamespace(phone="FAKE private phone", api_hash="FAKE private hash")
    record = safe_error(error)
    assert record["error_type"] == "RuntimeError"
    assert record["request_type"] == "SimpleNamespace"
    assert "FAKE private" not in json.dumps(record)


def test_diagnostics_record_only_allowlisted_rpc_identifiers():
    error = RuntimeError("FAKE private server detail")
    error.message, error.code = "BALANCE_TOO_LOW", 400
    record = safe_error(error)
    assert record["rpc_error"] == "BALANCE_TOO_LOW"
    assert record["rpc_status"] == 400
    error.message = "FAKE private phone/code/hash"
    record = safe_error(error)
    assert "rpc_error" not in record
    assert "FAKE private" not in json.dumps(record)


def test_diagnostics_keep_new_sdk_rpc_codes_but_drop_text_and_numeric_data():
    rpc_error = type(
        "BadRequestError", (RuntimeError,), {"__module__": "telethon.errors.rpcbaseerrors"}
    )
    error = rpc_error("FAKE private detail")
    error.code, error.message = 400, "TEST_PROVIDER_TOKEN_REJECTED"
    assert safe_error(error)["rpc_error"] == "TEST_PROVIDER_TOKEN_REJECTED"
    for unsafe in ("FAKE private phone", "PRIVATE_HASH_12345", "12345"):
        error.message = unsafe
        assert "rpc_error" not in safe_error(error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["", "FAKE private phone", "0000000", "+9", "+99966FAKE0001", "Phone: +9996600001"]
)
async def test_owner_phone_reprompts_locally_without_echoing_invalid_input(
    monkeypatch, capsys, invalid
):
    entries = iter([invalid, "+999 (66) 000-01"])

    async def enter(prompt, *, hidden):
        assert hidden is True
        return next(entries)

    monkeypatch.setattr(login, "owner_input", enter)
    assert await login.owner_phone() == "+9996600001"
    output = capsys.readouterr().out
    assert "Invalid phone" in output
    if invalid:
        assert invalid not in output


@pytest.fixture
def login_errors(monkeypatch):
    errors = ModuleType("telethon.errors")
    errors.PhoneCodeInvalidError = type("PhoneCodeInvalidError", (Exception,), {})
    errors.SessionPasswordNeededError = type("SessionPasswordNeededError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "telethon.errors", errors)
    return errors


@pytest.mark.asyncio
async def test_code_correction_reuses_the_sent_code_and_keeps_credentials_private(
    monkeypatch, capsys, login_errors
):
    entries = iter(["FAKE wrong code", "FAKE corrected code"])

    async def enter(prompt, *, hidden):
        assert hidden is True
        return next(entries)

    monkeypatch.setattr(login, "owner_input", enter)
    client = SimpleNamespace(
        initialize_test_connection=AsyncMock(),
        send_code_request=AsyncMock(
            return_value=SimpleNamespace(phone_code_hash="FAKE code hash", type=SimpleNamespace())
        ),
        sign_in=AsyncMock(side_effect=[login_errors.PhoneCodeInvalidError(), None]),
    )
    await login.interactive_login(client, "+9996600001")
    assert client.send_code_request.await_count == 1
    assert client.sign_in.await_count == 2
    for call in client.sign_in.await_args_list:
        assert call.kwargs["phone_code_hash"] == "FAKE code hash"
    output = capsys.readouterr().out
    for private in ("FAKE", "+9996600001"):
        assert private not in output


@pytest.mark.asyncio
async def test_invalid_codes_stop_after_three_owner_entered_attempts(monkeypatch, login_errors):
    enter = AsyncMock(return_value="FAKE invalid code")
    monkeypatch.setattr(login, "owner_input", enter)
    client = SimpleNamespace(
        initialize_test_connection=AsyncMock(),
        send_code_request=AsyncMock(
            return_value=SimpleNamespace(phone_code_hash="FAKE hash", type=SimpleNamespace())
        ),
        sign_in=AsyncMock(side_effect=login_errors.PhoneCodeInvalidError()),
    )
    with pytest.raises(login_errors.PhoneCodeInvalidError):
        await login.interactive_login(client, "+9996600001")
    assert client.send_code_request.await_count == 1
    assert client.sign_in.await_count == enter.await_count == 3


@pytest.mark.asyncio
async def test_two_factor_is_entered_hidden_only_after_telegram_requests_it(
    monkeypatch, capsys, login_errors
):
    enter = AsyncMock(side_effect=["FAKE code", "FAKE password"])
    monkeypatch.setattr(login, "owner_input", enter)
    client = SimpleNamespace(
        initialize_test_connection=AsyncMock(),
        send_code_request=AsyncMock(
            return_value=SimpleNamespace(phone_code_hash="FAKE hash", type=SimpleNamespace())
        ),
        sign_in=AsyncMock(side_effect=[login_errors.SessionPasswordNeededError(), None]),
    )
    await login.interactive_login(client, "+9996600001")
    assert client.send_code_request.await_count == 1
    assert client.sign_in.await_args.kwargs == {"password": "FAKE password"}
    assert all(call.kwargs["hidden"] is True for call in enter.await_args_list)
    assert "FAKE" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_empty_codes_never_call_sign_in_or_request_another_code(monkeypatch, login_errors):
    monkeypatch.setattr(login, "owner_input", AsyncMock(return_value=""))
    client = SimpleNamespace(
        initialize_test_connection=AsyncMock(),
        send_code_request=AsyncMock(
            return_value=SimpleNamespace(phone_code_hash="FAKE hash", type=SimpleNamespace())
        ),
        sign_in=AsyncMock(),
    )
    with pytest.raises(E2EBlocked):
        await login.interactive_login(client, "+9996600001")
    assert client.send_code_request.await_count == 1
    assert client.sign_in.await_count == 0


@pytest.mark.asyncio
async def test_non_code_rpc_failure_stops_without_retrying_login(monkeypatch, login_errors):
    monkeypatch.setattr(login, "owner_input", AsyncMock(return_value="FAKE code"))
    client = SimpleNamespace(
        initialize_test_connection=AsyncMock(),
        send_code_request=AsyncMock(
            return_value=SimpleNamespace(phone_code_hash="FAKE hash", type=SimpleNamespace())
        ),
        sign_in=AsyncMock(side_effect=TimeoutError()),
    )
    with pytest.raises(TimeoutError):
        await login.interactive_login(client, "+9996600001")
    assert client.send_code_request.await_count == client.sign_in.await_count == 1


@pytest.mark.asyncio
async def test_login_reinitializes_protocol_after_waiting_for_owner_input(
    monkeypatch, login_errors
):
    initialized = True

    async def initialize():
        nonlocal initialized
        initialized = True

    async def enter(prompt, *, hidden):
        nonlocal initialized
        initialized = False
        return "FAKE locally entered code"

    async def sign_in(*args, **kwargs):
        assert initialized, "ConnectionNotInited: user input outlived protocol initialization"

    monkeypatch.setattr(login, "owner_input", enter)
    client = SimpleNamespace(
        initialize_test_connection=AsyncMock(side_effect=initialize),
        send_code_request=AsyncMock(
            return_value=SimpleNamespace(phone_code_hash="FAKE hash", type=SimpleNamespace())
        ),
        sign_in=AsyncMock(side_effect=sign_in),
    )
    await login.interactive_login(client, "+9996600001")
    assert client.send_code_request.await_count == client.sign_in.await_count == 1
