"""Real Test Server connections. Never imported by the application runtime."""

import asyncio
from contextlib import asynccontextmanager
from copy import copy
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from telethon import TelegramClient, functions
from telethon.sessions import SQLiteSession
from telethon.tl.alltlobjects import LAYER

from telebotshop.bootstrap import create_bot
from telebotshop.infrastructure.database.readiness import database_ready
from tests.e2e.safety import (
    DATABASE_MARKER,
    TEST_DCS,
    E2EBlocked,
    require_test_session,
    session_path,
    validate_actor,
)


class TestClient(TelegramClient):
    async def initialize_test_connection(self):
        # shortcut: private Telethon 1.45 metadata; recheck when upgrading the SDK.
        init = copy(self._init_request)
        init.query = functions.help.GetConfigRequest()
        server = await self(functions.InvokeWithLayerRequest(LAYER, init))
        if server.test_mode is not True:
            raise E2EBlocked("The connected MTProto server is not in test mode")

    async def _get_dc(self, dc_id, cdn=False, ipv6=False):
        dc = await super()._get_dc(dc_id, cdn=cdn, ipv6=ipv6)
        require_test_session(
            SimpleNamespace(dc_id=dc.id, server_address=dc.ip_address, port=dc.port)
        )
        return dc


@asynccontextmanager
async def target(config):
    engine = create_async_engine(config.bot.database_url.get_secret_value(), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            marker = await connection.scalar(
                text(
                    "SELECT shobj_description(oid,'pg_database') "
                    "FROM pg_database WHERE datname=current_database()"
                )
            )
            if marker != DATABASE_MARKER:
                raise E2EBlocked("The acceptance database ownership marker is missing")
        if not await database_ready(engine):
            raise E2EBlocked("The acceptance schema is not at the application head")
        async with create_bot(config.bot) as bot:
            me = await bot.get_me()
            if me.id != int(config.bot.bot_token.get_secret_value().split(":", 1)[0]):
                raise E2EBlocked("Bot identity does not match the configured Test credential")
            if (await bot.get_webhook_info()).url:
                raise E2EBlocked("Acceptance expects the existing polling transport")
            yield engine, bot, me
    finally:
        await engine.dispose()


@asynccontextmanager
async def user_client(config, actor, *, login=False):
    path = session_path(config.root, actor)
    if not login and not path.is_file():
        raise E2EBlocked("Run the interactive Test Server login command first")
    path.parent.mkdir(parents=True, exist_ok=True)
    session = SQLiteSession(str(path))
    if not session.server_address:
        session.set_dc(2, TEST_DCS[2], 80)
    require_test_session(session)
    session.save_entities = False
    client = TestClient(
        session,
        config.api_id,
        config.api_hash.get_secret_value(),
        request_retries=0,
        connection_retries=1,
        auto_reconnect=False,
        flood_sleep_threshold=0,
        raise_last_call_error=True,
        timeout=8,
    )
    try:
        async with asyncio.timeout(30):
            await client.connect()
            await client.initialize_test_connection()
        yield client
    finally:
        await client.disconnect()


async def require_actor(client, config, actor):
    if not await client.is_user_authorized():
        raise E2EBlocked("The Test user session is not authorized; Owner must log in")
    user = await client.get_me()
    validate_actor(user, config.bot.owner_id, actor)
    return user
