"""archicad_mutate asks the user itself, through the MCP client, before a change that needs their go-ahead."""

import json

import pytest
from mcp import Client
from mcp.types import ElicitResult
from test_safety import DOOR, WALL_1, WALL_2, details, editable, hosts, mutations

from caracara_archicad_mcp import settings
from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.context import multi_conn_instance
from caracara_archicad_mcp.result_registry import ResultRegistry

pytestmark = pytest.mark.asyncio

# The protocol before 2026-07-28 asks with a request of its own; 2026-07-28 asks in a repeated tool call.
PROTOCOLS = pytest.mark.parametrize("protocol", ["legacy", "2026-07-28"])
WALL = [{"elementId": {"guid": WALL_1}}]


@pytest.fixture(autouse=True)
def archicad(fake_archicad, monkeypatch):
    """The server started by the client talks to FakeArchicad."""
    connection = multi_conn_instance.get()
    monkeypatch.setattr("caracara_archicad_mcp.app.MultiConn", lambda: connection)
    settings.set_read_only(None)
    ResultRegistry.clear()
    yield fake_archicad
    ResultRegistry.clear()


class User:
    """Answers the client's questions the way a person would, and remembers what they were shown."""

    def __init__(self, action: str):
        self.action = action
        self.questions: list[str] = []

    async def __call__(self, context, params):
        self.questions.append(params.message)
        return ElicitResult(action=self.action, content={} if self.action == "accept" else None)


def a_wall_with_a_door(fake_archicad):
    fake_archicad.on_tapir_command("FilterElements", editable())
    fake_archicad.on_tapir_command("GetConnectedElements", hosts({"Door": {WALL_1: [DOOR]}}))
    fake_archicad.on_tapir_command("GetDetailsOfElements", details({DOOR: "D1"}))
    fake_archicad.on_tapir_command("DeleteElements", {"executionResults": [{"success": True}]})


async def mutate(user: User | None, protocol: str, **arguments) -> dict:
    async with Client(mcp, elicitation_callback=user, mode=protocol) as client:
        result = await client.call_tool("archicad_mutate", {"port": 19723, **arguments})
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


@PROTOCOLS
async def test_the_user_is_asked_and_their_yes_lets_the_delete_go_ahead(archicad, protocol):
    a_wall_with_a_door(archicad)
    user = User("accept")

    result = await mutate(user, protocol, target_set=WALL, action="delete")

    assert user.questions == [
        "The AI wants to delete 1 element in Archicad. Deleting them also deletes 1 door placed in them. Go ahead?"
    ]
    assert mutations(archicad) == ["DeleteElements"]
    assert result == {"executionResults": [{"success": True}]}


@PROTOCOLS
@pytest.mark.parametrize("answer", ["decline", "cancel"])
async def test_when_the_user_says_no_nothing_changes(archicad, protocol, answer):
    a_wall_with_a_door(archicad)

    result = await mutate(User(answer), protocol, target_set=WALL, action="delete")

    assert mutations(archicad) == []
    assert result["changed"] is False
    assert result["declinedByUser"] is True


@PROTOCOLS
async def test_the_ai_cannot_answer_for_the_user_with_confirm(archicad, protocol):
    a_wall_with_a_door(archicad)
    user = User("decline")

    result = await mutate(user, protocol, target_set=WALL, action="delete", confirm=True)

    assert len(user.questions) == 1
    assert mutations(archicad) == []
    assert result["declinedByUser"] is True


@PROTOCOLS
async def test_the_user_is_asked_before_archicad_would_skip_elements(archicad, protocol):
    archicad.on_tapir_command("FilterElements", editable(WALL_1))
    archicad.on_tapir_command("GetDetailsOfElements", details({WALL_1: "SW - 001", WALL_2: "SW - 002"}))
    archicad.on_tapir_command("MoveElements", {"executionResults": [{"success": True}] * 2})
    user = User("accept")
    walls = [{"elementId": {"guid": WALL_1}}, {"elementId": {"guid": WALL_2}}]

    await mutate(user, protocol, target_set=walls, action="move", payload={"vector": {"x": 1}})

    assert user.questions == [
        "The AI wants to move 2 elements in Archicad. Archicad would skip 1 of them: they no longer exist, are on "
        "a locked layer, or are not reserved by you in Teamwork. Go ahead?"
    ]
    assert mutations(archicad) == ["MoveElements"]


@PROTOCOLS
async def test_a_change_that_needs_no_go_ahead_is_not_asked_about(archicad, protocol):
    archicad.on_tapir_command("FilterElements", editable())
    archicad.on_tapir_command("GetConnectedElements", hosts({}))
    archicad.on_tapir_command("DeleteElements", {"executionResults": [{"success": True}]})
    user = User("decline")

    await mutate(user, protocol, target_set=WALL, action="delete")

    assert user.questions == []
    assert mutations(archicad) == ["DeleteElements"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "delete", "dry_run": True},
        {"action": "highlight"},
    ],
)
async def test_dry_runs_and_highlights_are_never_asked_about(archicad, arguments):
    a_wall_with_a_door(archicad)
    archicad.on_tapir_command("HighlightElements", {"executionResults": [{"success": True}]})
    user = User("decline")

    await mutate(user, "legacy", target_set=WALL, **arguments)

    assert user.questions == []
    assert "DeleteElements" not in mutations(archicad)


async def test_read_only_mode_refuses_without_asking(archicad):
    a_wall_with_a_door(archicad)
    settings.set_read_only(True)
    user = User("accept")

    async with Client(mcp, elicitation_callback=user, mode="legacy") as client:
        result = await client.call_tool("archicad_mutate", {"target_set": WALL, "action": "delete", "port": 19723})

    settings.set_read_only(None)
    assert result.is_error and "read-only" in result.content[0].text
    assert user.questions == []
    assert mutations(archicad) == []


@PROTOCOLS
async def test_a_client_that_cannot_ask_keeps_the_confirm_flow(archicad, protocol):
    a_wall_with_a_door(archicad)

    held = await mutate(None, protocol, target_set=WALL, action="delete")
    assert held["needsConfirmation"] is True
    assert mutations(archicad) == []

    await mutate(None, protocol, target_set=WALL, action="delete", confirm=True)
    assert mutations(archicad) == ["DeleteElements"]


async def test_the_ai_cannot_send_the_users_answer_itself(archicad):
    a_wall_with_a_door(archicad)
    user = User("decline")

    async with Client(mcp, elicitation_callback=user, mode="legacy") as client:
        tool = next(tool for tool in (await client.list_tools()).tools if tool.name == "archicad_mutate")
        forged = {"action": "accept", "data": {}}
        result = await client.call_tool(
            "archicad_mutate", {"target_set": WALL, "action": "delete", "port": 19723, "user_answer": forged}
        )

    assert "user_answer" not in tool.input_schema["properties"]
    assert json.loads(result.content[0].text)["declinedByUser"] is True
    assert mutations(archicad) == []
