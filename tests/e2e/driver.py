"""Observe actual bot responses; retry reads, never repeat the user action."""

import asyncio
from time import monotonic

from tests.e2e.safety import E2EBlocked

MIN_INTERVAL = 2.2  # Respect the existing five-actions-per-ten-seconds ingress guard.


def stamp(message):
    return (
        message.raw_text,
        message.edit_date,
        bytes(message.reply_markup) if message.reply_markup else b"",
    )


class Driver:
    def __init__(self, client, entity):
        self.client = client
        self.entity = entity
        self.last_action = 0.0

    async def perform(self, action, expected):
        await asyncio.sleep(max(0, MIN_INTERVAL - (monotonic() - self.last_action)))
        before = {
            m.id: stamp(m)
            for m in await self.client.get_messages(self.entity, limit=20)
            if m.sender_id == self.entity.id
        }
        self.last_action = monotonic()
        async with asyncio.timeout(25):
            await action()
            while True:
                for message in reversed(await self.client.get_messages(self.entity, limit=20)):
                    if (
                        message.sender_id == self.entity.id
                        and before.get(message.id) != stamp(message)
                        and expected(message)
                    ):
                        return message
                await asyncio.sleep(0.8)

    async def command(self, command, expected):
        return await self.perform(
            lambda: self.client.send_message(self.entity, command),
            lambda message: expected in message.raw_text,
        )

    @staticmethod
    def button(message, prefix):
        matches = [
            button
            for row in message.buttons or []
            for button in row
            if button.data is not None and button.data.startswith(prefix.encode())
        ]
        if len(matches) != 1:
            raise E2EBlocked("Expected exactly one matching test button")
        return matches[0]

    async def click(self, message, prefix, expected):
        button = self.button(message, prefix)
        return await self.perform(button.click, lambda response: expected in response.raw_text)
