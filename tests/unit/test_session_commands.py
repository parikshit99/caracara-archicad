"""CloseProject and NewProject discard unsaved changes, so they stay refused until the user allows them."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from payloads import archicad_call_tool

from caracara_archicad_mcp import safety
from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.server import main, parse_args

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    for name in ("CARACARA_MCP_ALLOW_SESSION_COMMANDS", "CARACARA_MCP_READ_ONLY", "TAPIR_MCP_READ_ONLY"):
        monkeypatch.delenv(name, raising=False)
    safety.set_read_only(None)
    safety.set_session_commands_allowed(None)
    yield
    safety.set_read_only(None)
    safety.set_session_commands_allowed(None)


@pytest.mark.parametrize(
    ("tool", "command", "extra"),
    [("project_close_project", "CloseProject", {}), ("project_new_project", "NewProject", {"params": {}})],
)
async def test_refused_unless_the_user_allows_them(fake_archicad, tool, command, extra):
    fake_archicad.on_tapir_command(command, {})
    arguments = {"port": fake_archicad.port, **extra}

    with pytest.raises(ToolError, match="--allow-session-commands"):
        await archicad_call_tool(tool, arguments)
    assert fake_archicad.calls == []

    safety.set_session_commands_allowed(True)
    await archicad_call_tool(tool, arguments)
    assert [name for name, _ in fake_archicad.calls] == [command]


async def test_read_only_mode_still_blocks_them_when_allowed(fake_archicad):
    safety.set_session_commands_allowed(True)
    safety.set_read_only(True)

    with pytest.raises(ToolError, match="read-only mode"):
        await archicad_call_tool("project_close_project", {"port": fake_archicad.port})
    assert fake_archicad.calls == []


def test_the_environment_can_allow_them(monkeypatch):
    assert not safety.session_commands_allowed()
    monkeypatch.setenv("CARACARA_MCP_ALLOW_SESSION_COMMANDS", "true")
    assert safety.session_commands_allowed()


def test_the_flag_is_off_by_default_and_reaches_the_safety_layer(monkeypatch):
    monkeypatch.setattr(mcp, "run", MagicMock())
    monkeypatch.setattr(sys, "argv", ["server.py"])
    assert not parse_args().allow_session_commands

    monkeypatch.setattr(sys, "argv", ["server.py", "--allow-session-commands"])
    main()
    assert safety.session_commands_allowed()
