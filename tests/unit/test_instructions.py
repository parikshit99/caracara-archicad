"""The guidance clients receive (server instructions and the working-in-archicad skill) names only real tools."""

import re
from pathlib import Path

import pytest

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.instructions import SERVER_INSTRUCTIONS
from caracara_archicad_mcp.tools.custom.capture import TOOL_NAME as CAPTURE_TOOL
from caracara_archicad_mcp.tools.registration import register_all_tools

SKILL = Path(__file__).resolve().parents[2] / "skills" / "working-in-archicad" / "SKILL.md"
TOOL_NAME = r"\b(?:archicad|discovery)_[a-z_]+\b"


def _tools() -> set[str]:
    register_all_tools()
    return {tool.name for tool in mcp._tool_manager.list_tools()} | {CAPTURE_TOOL}


def test_clients_receive_the_instructions():
    assert mcp.instructions == SERVER_INSTRUCTIONS


def test_instructions_stay_short():
    """Clients add them to every conversation, so each word costs every user tokens."""
    assert len(SERVER_INSTRUCTIONS.split()) <= 250


@pytest.mark.parametrize(
    ("source", "text"),
    [("server instructions", SERVER_INSTRUCTIONS), ("skill", SKILL.read_text(encoding="utf-8"))],
    ids=["server-instructions", "skill"],
)
def test_guidance_names_only_tools_the_server_has(source, text):
    named = set(re.findall(TOOL_NAME, text))

    assert named, f"the {source} name no tools"
    assert named <= _tools(), named - _tools()
