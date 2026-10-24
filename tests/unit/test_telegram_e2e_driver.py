from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.e2e.driver import Driver
from tests.e2e.safety import E2EBlocked


def message(number, text, *, sender=42, buttons=None):
    return SimpleNamespace(
        id=number,
        raw_text=text,
        sender_id=sender,
        edit_date=None,
        reply_markup=None,
        buttons=buttons or [],
    )


@pytest.mark.asyncio
async def test_driver_waits_for_fresh_bot_response_and_ignores_notifications(monkeypatch):
    monkeypatch.setattr("tests.e2e.driver.MIN_INTERVAL", 0)
    old, noise, answer = message(1, "expected"), message(2, "notification"), message(3, "expected")
    client = SimpleNamespace(get_messages=AsyncMock(side_effect=[[old], [noise, old], [answer]]))
    action = AsyncMock()
    result = await Driver(client, SimpleNamespace(id=42)).perform(
        action, lambda m: m.raw_text == "expected"
    )
    assert result.id == 3
    action.assert_awaited_once()


@pytest.mark.asyncio
async def test_driver_never_retries_a_timed_out_external_action(monkeypatch):
    monkeypatch.setattr("tests.e2e.driver.MIN_INTERVAL", 0)
    client = SimpleNamespace(get_messages=AsyncMock(return_value=[]))
    action = AsyncMock(side_effect=TimeoutError)
    with pytest.raises(TimeoutError):
        await Driver(client, SimpleNamespace(id=42)).perform(action, lambda m: True)
    action.assert_awaited_once()


@pytest.mark.asyncio
async def test_driver_refuses_missing_or_ambiguous_button_before_sending():
    click = AsyncMock()
    buttons = [
        [
            SimpleNamespace(data=b"buy:first", click=click),
            SimpleNamespace(data=b"buy:second", click=click),
        ]
    ]
    driver = Driver(SimpleNamespace(), SimpleNamespace(id=42))
    for prefix in ("buy:", "missing:"):
        with pytest.raises(E2EBlocked):
            await driver.click(message(1, "product", buttons=buttons), prefix, "order")
    click.assert_not_awaited()
