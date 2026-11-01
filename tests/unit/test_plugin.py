"""The Claude plugin (.claude-plugin/plugin.json and its skill) starts the same server as the MCPB bundle."""

import json
import re
import tomllib
from pathlib import Path

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.tools.custom.capture import TOOL_NAME as CAPTURE_TOOL
from caracara_archicad_mcp.tools.registration import register_all_tools

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "working-in-archicad" / "SKILL.md"


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _plugin_server() -> dict:
    return _json(".claude-plugin/plugin.json")["mcpServers"]["caracara-archicad"]


def test_plugin_version_is_the_package_version():
    """scripts/release.py bumps both; a plugin left behind would keep its users on the old version."""
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert _json(".claude-plugin/plugin.json")["version"] == package


def test_plugin_offers_the_bundle_settings_with_the_same_safe_defaults():
    bundle = _json("manifest.json")["user_config"]
    plugin = _json(".claude-plugin/plugin.json")["userConfig"]

    assert plugin.keys() == bundle.keys()
    for key, setting in plugin.items():
        assert setting["default"] is False, key
        assert (setting["title"], setting["description"]) == (bundle[key]["title"], bundle[key]["description"]), key


def test_plugin_starts_the_server_like_the_bundle():
    bundle = _json("manifest.json")["server"]["mcp_config"]
    plugin = _plugin_server()

    assert plugin["command"] == bundle["command"]
    assert plugin["args"] == [arg.replace("${__dirname}", "${CLAUDE_PLUGIN_ROOT}") for arg in bundle["args"]]
    assert plugin["env"] == bundle["env"]


def test_plugin_privacy_policy_is_a_page_in_this_repository():
    """The directory listing links to it; a renamed or moved file would leave a dead link there."""
    url = _json(".claude-plugin/plugin.json")["privacyPolicyUrl"]
    prefix = "https://github.com/parikshit99/Caracara-Archicad-MCP/blob/master/"

    assert url.startswith(prefix)
    assert (ROOT / url.removeprefix(prefix)).is_file()


def test_skill_names_only_tools_the_server_has():
    register_all_tools()
    tools = {tool.name for tool in mcp._tool_manager.list_tools()} | {CAPTURE_TOOL}
    named = set(re.findall(r"`((?:archicad|discovery)_\w+)`", SKILL.read_text(encoding="utf-8")))

    assert named, "the skill names no tools"
    assert named <= tools, named - tools
