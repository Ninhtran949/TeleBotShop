from unittest.mock import Mock

import pytest

from telebotshop import bootstrap
from telebotshop.infrastructure.config import BotSettings, InventorySettings
from telebotshop.infrastructure.support import TermsConfigurationError
from tests.telegram import FAKE_TOKEN


@pytest.mark.asyncio
async def test_invalid_public_terms_fail_before_database_or_telegram(monkeypatch, tmp_path):
    path = tmp_path / "FAKE_PRIVATE_terms.md"
    path.write_text("[DD/MM/YYYY]", encoding="utf-8")
    settings = BotSettings(
        app_env="test",
        database_url="postgresql+asyncpg://fake:fake-password@127.0.0.1/fake",
        bot_token=FAKE_TOKEN,
        owner_id=9001,
        shop_terms_path=path,
    )
    database, bot = Mock(), Mock()
    monkeypatch.setattr(bootstrap, "create_async_engine", database)
    monkeypatch.setattr(bootstrap, "Bot", bot)
    with pytest.raises(TermsConfigurationError) as failure:
        await bootstrap.run_polling(settings, Mock(spec=InventorySettings))
    assert "FAKE_PRIVATE" not in str(failure.value)
    database.assert_not_called()
    bot.assert_not_called()
