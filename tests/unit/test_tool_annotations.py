import json
from pathlib import Path

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.tools.custom import functions  # noqa: F401  registers the four MCP tools
from caracara_archicad_mcp.tools.custom.build import ELEMENT_COMMANDS
from caracara_archicad_mcp.tools.custom.capture import TOOL_NAME as CAPTURE_TOOL
from caracara_archicad_mcp.tools.registration import register_all_tools

# openWorldHint differs across the read-only tools: discovery reaches out to
# whatever Archicad instances happen to be running, while the other two read
# the command catalogue compiled into this package.
READ_ONLY_TOOLS = {
    "discovery_list_active_archicads": True,
    "archicad_list_commands": False,
    "archicad_get_command_schema": False,
    "archicad_get_job": False,
    "archicad_read_tool": True,
}


def _tools_by_name() -> dict:
    return {tool.name: tool for tool in mcp._tool_manager.list_tools()}


def test_discovery_and_schema_tools_are_read_only():
    tools = _tools_by_name()
    for name, open_world in READ_ONLY_TOOLS.items():
        annotations = tools[name].annotations
        assert annotations is not None, f"{name} has no annotations"
        assert annotations.read_only_hint is True, name
        assert annotations.destructive_hint is False, name
        assert annotations.idempotent_hint is True, name
        assert annotations.open_world_hint is open_world, name


def test_call_tool_is_write_delete():
    annotations = _tools_by_name()["archicad_call_tool"].annotations
    assert annotations is not None
    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is True
    assert annotations.idempotent_hint is False
    assert annotations.open_world_hint is True


def test_build_tool_adds_without_destroying():
    register_all_tools()
    annotations = _tools_by_name()["archicad_build"].annotations
    assert annotations is not None
    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is False
    assert annotations.idempotent_hint is False
    assert annotations.open_world_hint is True


def test_call_tool_points_to_the_build_tool_for_every_type_it_builds():
    description = _tools_by_name()["archicad_call_tool"].description
    assert "archicad_build" in description
    for element_type in ELEMENT_COMMANDS:
        assert element_type.lower() in description, element_type


def test_manifest_lists_every_tool():
    """REVIEW.md R21: the bundle's tool list, shown to users before they install, matches the server."""
    register_all_tools()
    manifest = json.loads((Path(__file__).resolve().parents[2] / "manifest.json").read_text(encoding="utf-8"))

    # The server removes the capture tool at startup when images are off; the manifest lists it either way.
    assert {tool["name"] for tool in manifest["tools"]} == set(_tools_by_name()) | {CAPTURE_TOOL}
