import sys

import pytest

from caracara_archicad_mcp import settings
from caracara_archicad_mcp.server import parse_args

SWITCHES = [
    ("READ_ONLY", settings.read_only, settings.set_read_only),
    ("ALLOW_IMAGES", settings.images_allowed, settings.set_images_allowed),
    ("ALLOW_SESSION_COMMANDS", settings.session_commands_allowed, settings.set_session_commands_allowed),
]


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    for name in ("READ_ONLY", "ALLOW_IMAGES", "ALLOW_SESSION_COMMANDS", "PORT"):
        monkeypatch.delenv(f"CARACARA_MCP_{name}", raising=False)
        monkeypatch.delenv(f"TAPIR_MCP_{name}", raising=False)
    for _, _, set_switch in SWITCHES:
        set_switch(None)
    yield
    for _, _, set_switch in SWITCHES:
        set_switch(None)


@pytest.mark.parametrize(("name", "switch", "_"), SWITCHES)
def test_every_switch_is_off_by_default(name, switch, _):
    assert not switch()


@pytest.mark.parametrize(("name", "switch", "_"), SWITCHES)
def test_every_switch_reads_its_legacy_variable(monkeypatch, name, switch, _):
    monkeypatch.setenv(f"TAPIR_MCP_{name}", "true")
    assert switch()


@pytest.mark.parametrize(("name", "switch", "_"), SWITCHES)
def test_an_empty_variable_from_a_bundle_does_not_hide_the_legacy_one(monkeypatch, name, switch, _):
    monkeypatch.setenv(f"CARACARA_MCP_{name}", "")
    monkeypatch.setenv(f"TAPIR_MCP_{name}", "yes")
    assert switch()


@pytest.mark.parametrize(("name", "switch", "set_switch"), SWITCHES)
def test_a_value_set_by_the_server_wins_over_the_environment(monkeypatch, name, switch, set_switch):
    monkeypatch.setenv(f"CARACARA_MCP_{name}", "true")
    set_switch(False)
    assert not switch()


@pytest.mark.parametrize("value", ["0", "false", "no", "off", " "])
def test_anything_but_yes_leaves_a_switch_off(monkeypatch, value):
    monkeypatch.setenv("CARACARA_MCP_READ_ONLY", value)
    assert not settings.env_flag("READ_ONLY")


def test_env_prefers_the_caracara_variable(monkeypatch):
    monkeypatch.setenv("CARACARA_MCP_PORT", "9000")
    monkeypatch.setenv("TAPIR_MCP_PORT", "9001")
    assert settings.env("PORT") == "9000"


def test_an_empty_option_falls_back_to_its_default(monkeypatch):
    monkeypatch.setenv("CARACARA_MCP_PORT", "")
    monkeypatch.setattr(sys, "argv", ["server.py"])
    assert parse_args().port == 8000
