import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e.safety import (
    E2EBlocked,
    load_config,
    require_test_session,
    session_path,
    validate_actor,
    validate_invoice,
    validate_topup,
)


def environment(root: Path):
    (root / ".env").write_text("BOT_TOKEN=111111:FAKE_MAIN_" + "m" * 32, "utf-8")
    (root / ".env.telegram-test").write_text(
        "APP_ENV=test\nTELEGRAM_ENVIRONMENT=test\n"
        "BOT_TOKEN=222222:FAKE_TEST_" + "t" * 32 + "\nOWNER_ID=9001\n"
        "DATABASE_URL=postgresql+asyncpg://fake:fake@127.0.0.1:55432/telebotshop_telegram_test\n"
        "SHOP_TERMS_PATH=" + str(root / ".cache/telegram-test/terms-test.md") + "\n",
        "utf-8",
    )
    (root / ".env.telegram-e2e").write_text(
        "T14_API_ID=12345\nT14_API_HASH=" + "a" * 32 + "\n", "utf-8"
    )


def test_e2e_configuration_is_explicit_and_redacts_credentials(tmp_path, monkeypatch):
    environment(tmp_path)
    monkeypatch.setenv("TELEGRAM_ENVIRONMENT", "main")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://main:secret@main.invalid/main")
    config = load_config(tmp_path)
    assert config.bot.telegram_environment == "test"
    assert "telebotshop_telegram_test" in config.bot.database_url.get_secret_value()
    for secret in ("a" * 32, "FAKE_TEST_", "postgresql+asyncpg"):
        assert secret not in repr(config)


def test_e2e_reads_the_owner_phone_only_from_the_private_test_file(tmp_path, monkeypatch):
    environment(tmp_path)
    monkeypatch.setenv("Phone_Test_Server", "+19999999999")
    path = tmp_path / ".env.telegram-test"
    path.write_text(path.read_text("utf-8") + "Phone_Test_Server=+999 (66) 000-01\n", "utf-8")
    config = load_config(tmp_path)
    assert config.owner_phone.get_secret_value() == "+9996600001"
    assert "+9996600001" not in repr(config)
    assert "+9996600001" not in repr(config.owner_phone)


@pytest.mark.parametrize("phone", ["", "FAKE private phone"])
def test_e2e_rejects_malformed_configured_phone_without_echoing_it(tmp_path, phone):
    environment(tmp_path)
    path = tmp_path / ".env.telegram-test"
    path.write_text(path.read_text("utf-8") + f"Phone_Test_Server={phone}\n", "utf-8")
    with pytest.raises(E2EBlocked) as error:
        load_config(tmp_path)
    assert "FAKE private phone" not in str(error.value)


@pytest.mark.parametrize(
    "before,after",
    [
        ("APP_ENV=test", "APP_ENV=development"),
        ("TELEGRAM_ENVIRONMENT=test", "TELEGRAM_ENVIRONMENT=main"),
        ("127.0.0.1", "main.invalid"),
        (":55432/", ":5432/"),
        ("/telebotshop_telegram_test", "/telebotshop_dev"),
    ],
)
def test_e2e_refuses_wrong_environment_or_database(tmp_path, before, after):
    environment(tmp_path)
    path = tmp_path / ".env.telegram-test"
    path.write_text(path.read_text("utf-8").replace(before, after), "utf-8")
    with pytest.raises(E2EBlocked):
        load_config(tmp_path)


def test_e2e_refuses_reusing_main_bot_token(tmp_path):
    environment(tmp_path)
    test = (tmp_path / ".env.telegram-test").read_text("utf-8")
    token = next(line for line in test.splitlines() if line.startswith("BOT_TOKEN="))
    (tmp_path / ".env").write_text(token, "utf-8")
    with pytest.raises(E2EBlocked):
        load_config(tmp_path)


@pytest.mark.parametrize("value", ["", "T14_API_ID=no\nT14_API_HASH=secret", "T14_API_ID=0"])
def test_e2e_missing_or_invalid_api_credentials_are_sanitized(tmp_path, value):
    environment(tmp_path)
    (tmp_path / ".env.telegram-e2e").write_text(value, "utf-8")
    with pytest.raises(E2EBlocked) as error:
        load_config(tmp_path)
    assert "secret" not in str(error.value)


def test_e2e_session_path_cannot_select_an_arbitrary_account(tmp_path):
    assert session_path(tmp_path, "owner").is_relative_to(tmp_path)
    with pytest.raises(E2EBlocked):
        session_path(tmp_path, "../../main")


@pytest.mark.parametrize(
    "address,port", [("149.154.167.51", 443), ("127.0.0.1", 80), ("149.154.167.40", 22)]
)
def test_e2e_refuses_existing_non_test_session_before_connect(address, port):
    with pytest.raises(E2EBlocked):
        require_test_session(SimpleNamespace(dc_id=2, server_address=address, port=port))


def test_e2e_accepts_only_the_pinned_test_session_endpoint():
    require_test_session(SimpleNamespace(dc_id=2, server_address="149.154.167.40", port=80))


@pytest.mark.parametrize("field,value", [("test", False), ("currency", "USD"), ("total_amount", 2)])
def test_e2e_refuses_non_test_or_unexpected_invoice_before_payment(field, value):
    media = SimpleNamespace(test=True, currency="XTR", total_amount=1)
    setattr(media, field, value)
    form = SimpleNamespace(
        bot_id=42,
        invoice=SimpleNamespace(test=True, currency="XTR", prices=[SimpleNamespace(amount=1)]),
    )
    with pytest.raises(E2EBlocked):
        validate_invoice(media, form, 42)


def test_e2e_checks_merchant_and_form_price_independently_of_message():
    media = SimpleNamespace(test=True, currency="XTR", total_amount=1)
    invoice = SimpleNamespace(test=True, currency="XTR", prices=[SimpleNamespace(amount=1)])
    form = SimpleNamespace(bot_id=42, invoice=invoice)
    validate_invoice(media, form, 42)
    form.bot_id = 99
    with pytest.raises(E2EBlocked):
        validate_invoice(media, form, 42)
    form.bot_id = 42
    invoice.prices[0].amount = 100
    with pytest.raises(E2EBlocked):
        validate_invoice(media, form, 42)


@pytest.mark.parametrize(
    "actor,user_id,is_bot",
    [
        ("owner", 102, False),
        ("buyer2", 9001, False),
        ("owner", 9001, True),
    ],
)
def test_e2e_refuses_wrong_user_session(actor, user_id, is_bot):
    with pytest.raises(E2EBlocked):
        validate_actor(SimpleNamespace(id=user_id, bot=is_bot), 9001, actor)


def test_e2e_checks_test_actor_identity():
    validate_actor(SimpleNamespace(id=9001, bot=False), 9001, "owner")
    validate_actor(SimpleNamespace(id=102, bot=False), 9001, "buyer2")


def topup_form():
    option = SimpleNamespace(stars=15, currency="VND", amount=5000)
    form = SimpleNamespace(
        invoice=SimpleNamespace(test=True, currency="VND", prices=[SimpleNamespace(amount=5000)]),
        native_provider="stripe",
        native_params=SimpleNamespace(data=json.dumps({"publishable_key": "pk_test_FAKE"})),
        url="https://i-bots.stel.com/invoice/FAKE",
    )
    return form, option


def test_topup_accepts_only_the_observed_fifteen_star_test_form():
    validate_topup(*topup_form())


@pytest.mark.parametrize("changed", ["live", "provider", "key", "host", "stars", "price"])
def test_topup_rejects_real_or_unexpected_payment_forms(changed):
    form, option = topup_form()
    if changed == "live":
        form.invoice.test = False
    elif changed == "provider":
        form.native_provider = "unexpected"
    elif changed == "key":
        form.native_params.data = json.dumps({"publishable_key": "pk_live_FAKE"})
    elif changed == "host":
        form.url = "https://unexpected.invalid/invoice/FAKE"
    elif changed == "stars":
        option.stars = 50
    else:
        form.invoice.prices[0].amount = 5001
    with pytest.raises(E2EBlocked):
        validate_topup(form, option)
