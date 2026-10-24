import pytest
from aiogram.client.telegram import PRODUCTION, TEST
from pydantic import ValidationError

from telebotshop.bootstrap import create_bot
from telebotshop.infrastructure.config import BotSettings, WebhookSettings
from tests.telegram import FAKE_TOKEN


@pytest.mark.asyncio
async def test_bot_api_environment_is_explicit_and_shared_by_all_sdk_methods():
    values = dict(
        app_env="test",
        database_url="postgresql+asyncpg://fake:fake@127.0.0.1/fake",
        bot_token=FAKE_TOKEN,
        owner_id=9001,
    )
    for environment, expected in [("main", PRODUCTION), ("test", TEST)]:
        settings = BotSettings(**values, telegram_environment=environment)
        bot = create_bot(settings)
        try:
            assert bot.session.api == expected
            assert ("/test/" in bot.session.api.api_url(FAKE_TOKEN, "refundStarPayment")) == (
                environment == "test"
            )
            assert ("/test/" in bot.session.api.file_url(FAKE_TOKEN, "fake/file")) == (
                environment == "test"
            )
        finally:
            await bot.session.close()
    assert BotSettings(**values).telegram_environment == "main"
    with pytest.raises(ValidationError):
        BotSettings(**values, telegram_environment="https://untrusted.invalid")
    secret = "FAKE_SECRET_" + "x" * 32
    webhook = WebhookSettings(**values, webhook_secret=secret)
    assert secret not in repr(webhook)
    for invalid in ("short", "x" * 257, "x" * 32 + "\n", "x" * 32 + "é"):
        with pytest.raises(ValidationError) as error:
            WebhookSettings(**values, webhook_secret=invalid)
        assert invalid not in str(error.value)
