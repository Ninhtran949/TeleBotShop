from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.e2e import operations
from tests.e2e.safety import E2EBlocked


def actor(identifier, *, bot=False):
    return SimpleNamespace(id=identifier, bot=bot)


def test_support_pair_cli_rejects_a_buyer_operator_without_running_clients(monkeypatch, capsys):
    from tests.e2e import __main__ as entry

    def unexpected_run(args):
        raise AssertionError("Client execution started")

    monkeypatch.setattr(entry, "run", unexpected_run)
    monkeypatch.setattr("sys.argv", ["e2e", "support-pair", "--actor", "buyer2"])
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert error.value.code == 2
    assert "support-pair requires --actor owner" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "buyer"),
    [(actor(7), actor(8)), (actor(41), actor(41)), (actor(41), actor(7, bot=True))],
)
async def test_support_pair_rejects_invalid_actors_before_any_client_access(tmp_path, owner, buyer):
    config = SimpleNamespace(root=tmp_path, bot=SimpleNamespace(owner_id=41))
    with pytest.raises(E2EBlocked):
        await operations.support_pair(config, None, None, None, owner, buyer)


@pytest.mark.asyncio
async def test_support_pair_rejects_a_different_merchant_before_ticket_creation(tmp_path):
    config = SimpleNamespace(root=tmp_path, bot=SimpleNamespace(owner_id=41))
    merchant = SimpleNamespace(id=99, username="test_bot", bot=True)
    wrong = SimpleNamespace(id=100, bot=True)
    owner_client = SimpleNamespace(get_entity=AsyncMock(return_value=merchant))
    buyer_client = SimpleNamespace(get_entity=AsyncMock(return_value=wrong))
    with pytest.raises(E2EBlocked):
        await operations.support_pair(
            config, owner_client, buyer_client, merchant, actor(41), actor(7)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("leaked_body", [False, True])
async def test_support_pair_reads_reply_as_buyer_and_requires_metadata_only_notification(
    tmp_path, monkeypatch, leaked_body
):
    config = SimpleNamespace(root=tmp_path, bot=SimpleNamespace(owner_id=41))
    merchant = SimpleNamespace(id=99, username="test_bot", bot=True)
    owner_client = SimpleNamespace(actor=41, get_entity=AsyncMock(return_value=merchant))
    buyer_client = SimpleNamespace(actor=7, get_entity=AsyncMock(return_value=merchant))
    ticket = uuid4()
    state, passed = {}, []

    class FakeDriver:
        def __init__(self, client, entity):
            self.actor = client.actor

        async def command(self, command, expected):
            if command == "/start":
                state["started"] = self.actor
            elif command.startswith("/ticket GENERAL\n"):
                state["customer"] = self.actor
            elif command.startswith("/ticket_assign "):
                assert self.actor == 41
            elif command.startswith("/staff_reply "):
                assert self.actor == state["assigned"] == 41
                state["reply"] = command.split("\n", 1)[1]
            elif command == f"/ticket {ticket}":
                assert self.actor == state["customer"] == 7 and state["reply"]
                state["read_by"] = self.actor
            else:
                raise AssertionError("Unexpected test operation")
            return SimpleNamespace(raw_text=expected)

        @staticmethod
        def button(message, prefix):
            return SimpleNamespace(data=f"sp:view:{ticket}".encode())

        async def click(self, message, prefix, expected):
            if prefix == "sp:confirm:":
                assert self.actor == 41
                state["assigned"] = self.actor
            return SimpleNamespace(raw_text=expected)

        async def perform(self, action, predicate):
            await action()
            body = (
                "Ticket của bạn có phản hồi; mở thread để đọc\n"
                f"Mã tham chiếu: {ticket}\n/ticket {ticket}"
            )
            if leaked_body:
                body += "\n" + state["reply"]
            message = SimpleNamespace(raw_text=body)
            assert predicate(message), "The notification was not observed"
            state["notification_received_by"] = self.actor
            return message

    class FakeEvidence:
        def __init__(self, root, actor):
            assert actor == "buyer2"

        def passed(self, case, **kwargs):
            passed.append(case)

    monkeypatch.setattr(operations, "Driver", FakeDriver)
    monkeypatch.setattr(operations, "Evidence", FakeEvidence)
    if leaked_body:
        with pytest.raises(E2EBlocked):
            await operations.support_pair(
                config, owner_client, buyer_client, merchant, actor(41), actor(7)
            )
        assert not passed
    else:
        await operations.support_pair(
            config, owner_client, buyer_client, merchant, actor(41), actor(7)
        )
        assert state["read_by"] == state["notification_received_by"] == 7
        assert state["customer"] != state["assigned"]
        assert passed == ["support.cross_user_thread", "support.cross_user_notification"]
