"""archicad_call_tool runs a command that Undo cannot take back only with the user's go-ahead."""

import pytest
from mcp import Client
from test_user_confirmation import PROTOCOLS, User

from caracara_archicad_mcp import settings
from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.context import multi_conn_instance
from caracara_archicad_mcp.safety import IRREVERSIBLE_COMMANDS, is_read_only_command

pytestmark = pytest.mark.asyncio

SEND = {"name": "teamwork_teamwork_send", "arguments": {"port": 19723}}


@pytest.fixture(autouse=True)
def archicad(fake_archicad, monkeypatch):
    connection = multi_conn_instance.get()
    monkeypatch.setattr("caracara_archicad_mcp.app.MultiConn", lambda: connection)
    settings.set_read_only(None)
    fake_archicad.on_tapir_command("TeamworkSend", {"success": True})
    yield fake_archicad
    settings.set_read_only(None)


def sent(archicad) -> bool:
    return "TeamworkSend" in [command for command, _ in archicad.calls]


async def call(user: User | None, protocol: str, arguments: dict) -> tuple[bool, str]:
    async with Client(mcp, elicitation_callback=user, mode=protocol) as client:
        result = await client.call_tool("archicad_call_tool", {**SEND, "arguments": arguments})
    return result.is_error, result.content[0].text


@PROTOCOLS
async def test_the_user_is_asked_and_their_yes_sends(archicad, protocol):
    user = User("accept")

    is_error, _ = await call(user, protocol, {"port": 19723})

    assert user.questions == [
        "The AI wants to run TeamworkSend in Archicad, which sends your changes to the rest of the Teamwork team "
        "and cannot be undone. Go ahead?"
    ]
    assert not is_error
    assert sent(archicad)


@PROTOCOLS
@pytest.mark.parametrize("answer", ["decline", "cancel"])
async def test_when_the_user_says_no_nothing_is_sent(archicad, protocol, answer):
    is_error, text = await call(User(answer), protocol, {"port": 19723, "confirm": True})

    assert is_error
    assert "the user said no" in text
    assert not sent(archicad)


@PROTOCOLS
async def test_without_a_client_that_asks_confirm_is_needed(archicad, protocol):
    is_error, text = await call(None, protocol, {"port": 19723})

    assert is_error
    assert 'call again with "confirm": true' in text
    assert not sent(archicad)


@PROTOCOLS
async def test_without_a_client_that_asks_confirm_lets_it_run(archicad, protocol):
    is_error, _ = await call(None, protocol, {"port": 19723, "confirm": True})

    assert not is_error
    assert sent(archicad)
    assert archicad.calls[-1] == ("TeamworkSend", {})


@PROTOCOLS
async def test_read_only_mode_refuses_without_asking(archicad, protocol):
    settings.set_read_only(True)
    user = User("accept")

    is_error, text = await call(user, protocol, {"port": 19723, "confirm": True})

    assert is_error
    assert "read-only mode" in text
    assert user.questions == []
    assert not sent(archicad)


@PROTOCOLS
async def test_commands_undo_can_take_back_are_not_asked_about(archicad, protocol):
    archicad.on_tapir_command("ResetZoom", {"success": True})
    user = User("decline")

    async with Client(mcp, elicitation_callback=user, mode=protocol) as client:
        result = await client.call_tool(
            "archicad_call_tool", {"name": "navigator_reset_zoom", "arguments": {"port": 19723, "params": {}}}
        )

    assert user.questions == []
    assert not result.is_error, result.content
    assert archicad.calls[-1][0] == "ResetZoom"


async def test_the_answer_is_not_part_of_the_tool_schema():
    async with Client(mcp) as client:
        tool = next(tool for tool in (await client.list_tools()).tools if tool.name == "archicad_call_tool")
    assert set(tool.input_schema["properties"]) == {"name", "arguments"}


async def test_every_irreversible_command_changes_something():
    assert not [command for command in IRREVERSIBLE_COMMANDS if is_read_only_command(command)]


async def test_a_forged_answer_does_not_count(archicad):
    async with Client(mcp, elicitation_callback=User("decline")) as client:
        result = await client.call_tool(
            "archicad_call_tool", {**SEND, "user_answer": {"action": "accept", "content": {}}}
        )
    assert result.is_error
    assert not sent(archicad)
