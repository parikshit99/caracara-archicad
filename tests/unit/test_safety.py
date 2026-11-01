"""The safety layer: read-only mode, dry runs and the confirmation before deleting hosted elements."""

from __future__ import annotations

import sys

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from payloads import archicad_call_tool

from caracara_archicad_mcp import safety
from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom.meta_verbs import archicad_mutate
from caracara_archicad_mcp.tools.tool_registry import TOOL_DISCOVERY_CATALOG

WALL_1 = "00000000-0000-0000-0000-000000000001"
WALL_2 = "00000000-0000-0000-0000-000000000002"
DOOR = "00000000-0000-0000-0000-0000000000d1"
WINDOW = "00000000-0000-0000-0000-0000000000a1"


def _ids(*guids: str) -> list[dict]:
    return [{"elementId": {"guid": guid}} for guid in guids]


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv("CARACARA_MCP_READ_ONLY", raising=False)
    monkeypatch.delenv("TAPIR_MCP_READ_ONLY", raising=False)
    safety.set_read_only(None)
    ResultRegistry.clear()
    yield
    safety.set_read_only(None)
    ResultRegistry.clear()


def hosts(per_type: dict[str, dict[str, list[str]]]):
    """GetConnectedElements answer: per connected type, the hosted GUIDs of each owner GUID."""

    def respond(params):
        hosted = per_type.get(params["connectedElementType"], {})
        owners = [item["elementId"]["guid"] for item in params["elements"]]
        return {"connectedElements": [{"elements": _ids(*hosted.get(owner, []))} for owner in owners]}

    return respond


def details(element_ids: dict[str, str]):
    """GetDetailsOfElements answer with only the Element ID; GUIDs not given no longer exist."""

    def respond(params):
        missing = {"error": {"code": -2130313115, "message": "Element does not exist."}}
        guids = [item["elementId"]["guid"] for item in params["elements"]]
        return {"detailsOfElements": [{"id": element_ids[g]} if g in element_ids else missing for g in guids]}

    return respond


def editable(*guids: str):
    """FilterElements answer for IsEditable: the given GUIDs, or every element asked about."""
    return lambda params: {"elements": _ids(*guids) if guids else params["elements"]}


def mutations(fake_archicad) -> list[str]:
    return [command for command, _ in fake_archicad.calls if not safety.is_read_only_command(command)]


# --- Which commands count as read-only ---------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "GetLayers",
        "Get3DBoundingBoxes",
        "API.GetAllElements",
        "IsAddOnCommandAvailable",
        "FilterElements",
        *sorted(safety.REVIEWED_COMMANDS),
    ],
)
def test_reading_and_display_commands_are_read_only(command):
    assert safety.is_read_only_command(command)


@pytest.mark.parametrize(
    "command",
    ["DeleteElements", "SaveProject", "ExportIssuesToBCF", "TeamworkSend", "ReserveElements", "Getaway", "Get"],
)
def test_changing_commands_are_not_read_only(command):
    assert not safety.is_read_only_command(command)


def test_every_registered_command_is_classified_as_expected(fake_archicad):
    """Guards the allowlist: only Get/Is/Filter commands, the display-only ones and the window picture pass
    in read-only mode."""
    reviewed = safety.REVIEWED_COMMANDS
    allowed = sorted(
        entry["title"] for entry in TOOL_DISCOVERY_CATALOG.values() if safety.is_read_only_command(entry["title"])
    )
    assert allowed
    assert all(title.startswith(("Get", "Is", "Filter")) or title in reviewed for title in allowed)
    unreviewed = [title for title in allowed if title not in reviewed]
    assert not any(title.startswith(("Set", "Create", "Delete", "Save", "Export", "Teamwork")) for title in unreviewed)
    assert safety.WINDOW_IMAGE_COMMANDS.issubset(allowed)


# --- Read-only mode -------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_environment_switches_read_only_on(monkeypatch, value):
    monkeypatch.setenv("CARACARA_MCP_READ_ONLY", value)
    assert safety.read_only()


def test_read_only_is_off_by_default():
    assert not safety.read_only()


@pytest.mark.asyncio
async def test_read_only_refuses_commands_that_change_the_project(fake_archicad):
    safety.set_read_only(True)
    with pytest.raises(ToolError, match="read-only mode") as refusal:
        await archicad_call_tool(
            "elements_delete_elements", {"port": fake_archicad.port, "params": {"elements": _ids(WALL_1)}}
        )
    assert fake_archicad.calls == []
    # Only archicad_mutate actions can be previewed with a dry run.
    assert "dry_run" not in str(refusal.value)


@pytest.mark.asyncio
async def test_read_only_still_allows_reading(fake_archicad):
    safety.set_read_only(True)
    fake_archicad.on_tapir_command("GetConnectedElements", hosts({}))
    result = await archicad_call_tool(
        "elements_get_connected_elements",
        {"port": fake_archicad.port, "params": {"elements": _ids(WALL_1), "connectedElementType": "Door"}},
    )
    assert result == {"connectedElements": [{"elements": []}]}


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "payload"), [("delete", None), ("move", {"vector": {"x": 1}})])
async def test_read_only_refuses_mutations_before_contacting_archicad(fake_archicad, action, payload):
    safety.set_read_only(True)
    with pytest.raises(ToolError, match=r"read-only mode.*archicad_mutate with dry_run=true"):
        await archicad_mutate(_ids(WALL_1), action, payload, port=fake_archicad.port, confirm=True)
    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_read_only_allows_highlighting(fake_archicad):
    safety.set_read_only(True)
    fake_archicad.on_tapir_command("HighlightElements", {"success": True})
    assert await archicad_mutate(_ids(WALL_1), "highlight", port=fake_archicad.port) == {"success": True}


@pytest.mark.asyncio
async def test_read_only_allows_dry_runs(fake_archicad):
    safety.set_read_only(True)
    fake_archicad.on_tapir_command("FilterElements", {"elements": _ids(WALL_1)})
    fake_archicad.on_tapir_command("GetConnectedElements", hosts({}))
    report = await archicad_mutate(_ids(WALL_1), "delete", port=fake_archicad.port, dry_run=True)
    assert report["dryRun"] is True
    assert mutations(fake_archicad) == []


def test_server_flag_and_environment_turn_read_only_on(monkeypatch):
    from caracara_archicad_mcp.server import parse_args

    monkeypatch.setattr(sys, "argv", ["server.py", "--read-only"])
    assert parse_args().read_only
    monkeypatch.setattr(sys, "argv", ["server.py"])
    assert not parse_args().read_only
    monkeypatch.setenv("CARACARA_MCP_READ_ONLY", "true")
    assert parse_args().read_only


# --- Dry run --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_delete_reports_without_changing_anything(fake_archicad):
    fake_archicad.on_tapir_command("FilterElements", {"elements": _ids(WALL_1)})
    fake_archicad.on_tapir_command(
        "GetConnectedElements", hosts({"Door": {WALL_1: [DOOR]}, "Window": {WALL_2: [WINDOW]}})
    )
    fake_archicad.on_tapir_command("GetDetailsOfElements", details({WALL_2: "SW - 002", DOOR: "D1", WINDOW: "W1"}))

    report = await archicad_mutate(_ids(WALL_1, WALL_2), "delete", port=fake_archicad.port, dry_run=True)

    assert mutations(fake_archicad) == []
    assert report["dryRun"] is True
    assert report["elementCount"] == 2
    assert report["notEditableCount"] == 1
    assert report["notEditable"] == [{"elementId": {"guid": WALL_2}, "id": "SW - 002"}]
    assert report["missingCount"] == 0
    assert report["hostedElementCount"] == 2
    assert {"elementId": {"guid": DOOR}, "type": "Door", "id": "D1"} in report["hostedElements"]
    assert any("1 door, 1 window" in warning for warning in report["warnings"])
    assert any("locked layer" in warning for warning in report["warnings"])
    # The editability check leaves no stray set behind.
    assert ResultRegistry.has("set_elements_01")
    assert not ResultRegistry.has("set_filtered_01")


@pytest.mark.asyncio
async def test_dry_run_move_checks_editability_only(fake_archicad):
    fake_archicad.on_tapir_command("FilterElements", {"elements": _ids(WALL_1, WALL_2)})

    report = await archicad_mutate(
        _ids(WALL_1, WALL_2), "move", {"vector": {"x": 1}}, port=fake_archicad.port, dry_run=True
    )

    assert [command for command, _ in fake_archicad.calls] == ["FilterElements"]
    assert fake_archicad.calls[0][1] == {"elements": _ids(WALL_1, WALL_2), "filters": ["IsEditable"]}
    assert report == {"dryRun": True, "action": "move", "elementCount": 2, "missingCount": 0, "warnings": []}


@pytest.mark.asyncio
async def test_dry_run_rejects_unknown_actions(fake_archicad):
    with pytest.raises(ToolError, match="Unsupported action"):
        await archicad_mutate(_ids(WALL_1), "explode", port=fake_archicad.port, dry_run=True)
    assert fake_archicad.calls == []


# --- Confirmation before deleting hosted elements ----------------------------


@pytest.mark.asyncio
async def test_delete_that_would_remove_hosted_elements_waits_for_confirmation(fake_archicad):
    fake_archicad.on_tapir_command("FilterElements", editable())
    fake_archicad.on_tapir_command("GetConnectedElements", hosts({"Door": {WALL_1: [DOOR]}}))
    fake_archicad.on_tapir_command("GetDetailsOfElements", details({DOOR: "D1"}))

    result = await archicad_mutate(_ids(WALL_1), "delete", port=fake_archicad.port)

    assert mutations(fake_archicad) == []
    assert result["deleted"] is False
    assert result["needsConfirmation"] is True
    assert result["hostedElementCount"] == 1
    assert result["hostedElements"] == [{"elementId": {"guid": DOOR}, "type": "Door", "id": "D1"}]
    assert "1 door" in result["message"]


@pytest.mark.asyncio
async def test_confirmed_delete_goes_ahead(fake_archicad):
    fake_archicad.on_tapir_command("GetConnectedElements", hosts({"Door": {WALL_1: [DOOR]}}))
    fake_archicad.on_tapir_command("DeleteElements", {"executionResults": [{"success": True}]})

    result = await archicad_mutate(_ids(WALL_1), "delete", port=fake_archicad.port, confirm=True)

    assert mutations(fake_archicad) == ["DeleteElements"]
    assert result == {"executionResults": [{"success": True}]}


@pytest.mark.asyncio
async def test_delete_goes_ahead_when_the_hosted_elements_are_part_of_the_target(fake_archicad):
    fake_archicad.on_tapir_command("FilterElements", editable())
    fake_archicad.on_tapir_command("GetConnectedElements", hosts({"Door": {WALL_1: [DOOR]}}))
    fake_archicad.on_tapir_command("DeleteElements", {"executionResults": [{"success": True}] * 2})

    await archicad_mutate(_ids(WALL_1, DOOR), "delete", port=fake_archicad.port)

    assert mutations(fake_archicad) == ["DeleteElements"]


@pytest.mark.asyncio
async def test_delete_without_hosted_elements_needs_no_confirmation(fake_archicad):
    fake_archicad.on_tapir_command("FilterElements", editable())
    fake_archicad.on_tapir_command("GetConnectedElements", hosts({}))
    fake_archicad.on_tapir_command("DeleteElements", {"executionResults": [{"success": True}]})

    await archicad_mutate(_ids(WALL_1), "delete", port=fake_archicad.port)

    assert mutations(fake_archicad) == ["DeleteElements"]
    checked = [
        params["connectedElementType"] for command, params in fake_archicad.calls if command == "GetConnectedElements"
    ]
    assert checked == ["Door", "Window", "Skylight", "Opening"]


@pytest.mark.asyncio
async def test_hosted_check_reports_what_it_could_not_check(fake_archicad):
    def respond(params):
        if params["connectedElementType"] == "Opening":
            return {"error": {"code": 1, "message": "Opening is not supported"}}
        return hosts({})(params)

    fake_archicad.on_tapir_command("FilterElements", {"elements": _ids(WALL_1)})
    fake_archicad.on_tapir_command("GetConnectedElements", respond)

    report = await archicad_mutate(_ids(WALL_1), "delete", port=fake_archicad.port, dry_run=True)

    assert report["hostedElementCount"] == 0
    assert report["uncheckedHostedTypes"] == ["Opening: Opening is not supported"]


@pytest.mark.asyncio
async def test_dry_run_says_so_when_the_editability_check_fails(fake_archicad):
    fake_archicad.on_tapir_command("FilterElements", {"error": {"code": 1, "message": "unavailable"}})

    report = await archicad_mutate(_ids(WALL_1), "move", {"vector": {"x": 1}}, port=fake_archicad.port, dry_run=True)

    assert "notEditable" not in report
    assert report["warnings"] == ["Locked elements were not checked: Archicad gave no answer."]


# --- The check before every change -------------------------------------------


@pytest.mark.asyncio
async def test_change_waits_when_elements_of_the_set_were_deleted(fake_archicad):
    set_id = ResultRegistry.register(_ids(WALL_1, WALL_2), element_type="Wall")
    fake_archicad.on_tapir_command("FilterElements", editable(WALL_1))
    fake_archicad.on_tapir_command("GetDetailsOfElements", details({WALL_1: "SW - 001"}))

    result = await archicad_mutate(set_id, "move", {"vector": {"x": 1}}, port=fake_archicad.port)

    assert mutations(fake_archicad) == []
    assert result["changed"] is False
    assert result["needsConfirmation"] is True
    assert result["setAgeSeconds"] == 0
    assert result["missingCount"] == 1
    assert result["missing"] == [{"elementId": {"guid": WALL_2}}]
    assert "notEditable" not in result
    assert "1 of 2 elements" in result["message"]
    assert any("no longer exist" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_change_waits_when_elements_are_locked_and_names_them(fake_archicad):
    fake_archicad.on_tapir_command("FilterElements", editable(WALL_1))
    fake_archicad.on_tapir_command("GetDetailsOfElements", details({WALL_1: "SW - 001", WALL_2: "SW - 002"}))

    result = await archicad_mutate(_ids(WALL_1, WALL_2), "set_details", {"details": {}}, port=fake_archicad.port)

    assert mutations(fake_archicad) == []
    assert result["needsConfirmation"] is True
    assert result["missingCount"] == 0
    assert result["notEditable"] == [{"elementId": {"guid": WALL_2}, "id": "SW - 002"}]


@pytest.mark.asyncio
async def test_confirmed_change_goes_ahead_and_names_the_elements_archicad_skipped(fake_archicad):
    set_id = ResultRegistry.register(_ids(WALL_1, WALL_2), element_type="Wall")
    failure = {"success": False, "error": {"code": -2130312913, "message": "Locked layer."}}
    fake_archicad.on_tapir_command("MoveElements", {"executionResults": [{"success": True}, failure]})
    fake_archicad.on_tapir_command("GetDetailsOfElements", details({WALL_2: "SW - 002"}))

    result = await archicad_mutate(set_id, "move", {"vector": {"x": 1}}, port=fake_archicad.port, confirm=True)

    assert mutations(fake_archicad) == ["MoveElements"]
    assert "FilterElements" not in [command for command, _ in fake_archicad.calls]
    assert result["setAgeSeconds"] == 0
    assert "missingCount" not in result
    assert result["failedElements"] == [{"error": failure["error"], "elementId": {"guid": WALL_2}, "id": "SW - 002"}]


@pytest.mark.asyncio
async def test_change_of_a_current_set_reports_its_age_and_no_missing_elements(fake_archicad, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("caracara_archicad_mcp.result_registry.time.monotonic", lambda: clock[0])
    set_id = ResultRegistry.register(_ids(WALL_1), element_type="Wall")
    clock[0] = 1090.4
    fake_archicad.on_tapir_command("FilterElements", editable())
    fake_archicad.on_tapir_command("MoveElements", {"executionResults": [{"success": True}]})

    result = await archicad_mutate(set_id, "move", {"vector": {"x": 1}}, port=fake_archicad.port)

    assert result == {"executionResults": [{"success": True}], "setAgeSeconds": 90, "missingCount": 0}


@pytest.mark.asyncio
async def test_change_goes_ahead_with_a_warning_when_the_check_cannot_run(fake_archicad):
    fake_archicad.on_tapir_command("FilterElements", {"error": {"code": 1, "message": "unavailable"}})
    fake_archicad.on_tapir_command("MoveElements", {"executionResults": [{"success": True}]})

    result = await archicad_mutate(_ids(WALL_1), "move", {"vector": {"x": 1}}, port=fake_archicad.port)

    assert mutations(fake_archicad) == ["MoveElements"]
    assert result["warnings"] == ["Locked elements were not checked: Archicad gave no answer."]


@pytest.mark.asyncio
async def test_highlighting_is_not_checked(fake_archicad):
    fake_archicad.on_tapir_command("HighlightElements", {"success": True})

    result = await archicad_mutate(_ids(WALL_1), "highlight", port=fake_archicad.port)

    assert result == {"success": True}
    assert [command for command, _ in fake_archicad.calls] == ["HighlightElements"]


@pytest.mark.asyncio
async def test_unread_element_ids_leave_the_missing_count_open(fake_archicad):
    fake_archicad.on_tapir_command("FilterElements", editable(WALL_1))
    fake_archicad.on_tapir_command("GetDetailsOfElements", {"error": {"code": 1, "message": "unavailable"}})

    report = await archicad_mutate(
        _ids(WALL_1, WALL_2), "move", {"vector": {"x": 1}}, port=fake_archicad.port, dry_run=True
    )

    assert "missingCount" not in report
    assert report["notEditable"] == [{"elementId": {"guid": WALL_2}}]
    assert any("not told apart" in warning for warning in report["warnings"])


@pytest.mark.asyncio
async def test_dry_run_of_a_set_gives_its_age(fake_archicad):
    set_id = ResultRegistry.register(_ids(WALL_1), element_type="Wall")
    fake_archicad.on_tapir_command("FilterElements", editable())

    report = await archicad_mutate(set_id, "move", {"vector": {"x": 1}}, port=fake_archicad.port, dry_run=True)

    assert report["setAgeSeconds"] == 0
