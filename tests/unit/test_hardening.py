"""Offline checks for the security, memory and error-handling guarantees."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad import Port
from multiconn_archicad.clients.core.core_commands import CoreCommands
from multiconn_archicad.models.tapir.commands import IFCFileOperationParameters, OpenProjectParameters
from payloads import archicad_call_tool

from caracara_archicad_mcp import file_paths
from caracara_archicad_mcp.context import multi_conn_instance
from caracara_archicad_mcp.field_masking import prune_payload
from caracara_archicad_mcp.result_registry import STORAGE_SETS, ResultRegistry, SetProject
from caracara_archicad_mcp.safety import is_read_only_command
from caracara_archicad_mcp.tools import overrides
from caracara_archicad_mcp.tools.custom import meta_verbs
from caracara_archicad_mcp.tools.custom.meta_verbs import archicad_mutate, archicad_query

SRC = Path(__file__).resolve().parents[2] / "src"
ALIVE = "11111111-1111-1111-1111-111111111111"
GHOST = "22222222-2222-2222-2222-222222222222"
ROTATION = {"beginPoint": {"x": 1, "y": 0}, "endPoint": {"x": 0, "y": 1}, "origin": {"x": 0, "y": 0}}


def _guid(i: int) -> str:
    return f"00000000-0000-0000-0000-{i:012d}"


@pytest.fixture(autouse=True)
def clean_registry():
    ResultRegistry.clear()
    ResultRegistry.reset_limits()
    yield
    ResultRegistry.clear()
    ResultRegistry.reset_limits()


@pytest.fixture
def output_root(tmp_path):
    file_paths.set_output_root(tmp_path / "out")
    yield (tmp_path / "out").resolve()
    file_paths.set_output_root(None)


# --- Security -----------------------------------------------------------------


def test_no_dynamic_code_execution_in_server_sources():
    pattern = re.compile(r"\b(exec|eval)\s*\(|\brun_script\b|\bsubprocess\b|\bos\.system\b|__import__\s*\(")
    offenders = [
        f"{path.relative_to(SRC)}:{number}"
        for path in SRC.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


def test_relative_write_path_is_placed_under_output_root(output_root):
    assert Path(file_paths.confine_path("exports/model.pla")) == output_root / "exports" / "model.pla"


@pytest.mark.parametrize("raw", ["../escape.pla", "exports/../../escape.pla", "", "bad\x00name.pla"])
def test_escaping_or_malformed_write_paths_are_rejected(output_root, raw):
    with pytest.raises(ToolError):
        file_paths.confine_path(raw)


def test_absolute_write_path_outside_root_is_rejected(output_root, tmp_path):
    with pytest.raises(ToolError, match="outside the allowed output directory"):
        file_paths.confine_path(str(tmp_path / "elsewhere" / "model.pla"))


def test_read_paths_and_ifc_open_are_not_confined(output_root, tmp_path):
    outside = str(tmp_path / "project.pln")
    assert file_paths.confine_write_paths(OpenProjectParameters(projectFilePath=outside)).projectFilePath == outside
    ifc_open = IFCFileOperationParameters(method="open", ifcFilePath=outside)
    assert file_paths.confine_write_paths(ifc_open).ifcFilePath == outside
    ifc_save = IFCFileOperationParameters(method="save", ifcFilePath=outside)
    with pytest.raises(ToolError):
        file_paths.confine_write_paths(ifc_save)


@pytest.mark.asyncio
async def test_dispatch_rewrites_write_path_before_reaching_archicad(fake_archicad, output_root):
    fake_archicad.on_tapir_command("SaveProjectAsArchive", {"success": True})
    await archicad_call_tool(
        "project_save_project_as_archive",
        {"port": fake_archicad.port, "params": {"archiveFilePath": "backup.pla"}},
    )
    assert fake_archicad.calls[-1][1]["archiveFilePath"] == str(output_root / "backup.pla")


@pytest.mark.asyncio
async def test_dispatch_refuses_write_path_outside_root(fake_archicad, output_root, tmp_path):
    with pytest.raises(ToolError, match="outside the allowed output directory"):
        await archicad_call_tool(
            "project_save_project_as_archive",
            {"port": fake_archicad.port, "params": {"archiveFilePath": str(tmp_path / "x.pla")}},
        )
    assert fake_archicad.calls == []


# --- Memory bounds ------------------------------------------------------------


def test_registry_defaults_bound_sets_elements_and_idle_time():
    assert ResultRegistry.MAX_CAPACITY == 50
    assert ResultRegistry.MAX_TOTAL_ELEMENTS > 0
    assert ResultRegistry.TTL_SECONDS is not None


def test_registry_evicts_lru_sets_to_fit_element_budget():
    ResultRegistry.set_max_total_elements(10)
    first = ResultRegistry.register([{"elementId": {"guid": f"a{i}"}} for i in range(6)])
    second = ResultRegistry.register([{"elementId": {"guid": f"b{i}"}} for i in range(3)])
    third = ResultRegistry.register([{"elementId": {"guid": f"c{i}"}} for i in range(4)])
    assert not ResultRegistry.has(first)
    assert ResultRegistry.has(second) and ResultRegistry.has(third)


def test_registry_rejects_a_single_set_larger_than_the_budget():
    ResultRegistry.set_max_total_elements(5)
    with pytest.raises(ToolError, match="exceeds the virtual set limit"):
        ResultRegistry.register([{"elementId": {"guid": str(i)}} for i in range(6)])
    assert len(STORAGE_SETS) == 0


def test_registry_prefixes_from_arbitrary_input_are_bounded():
    assert ResultRegistry.register([], element_type="x" * 500) == "set_" + "x" * 32 + "s_01"
    for i in range(300):
        ResultRegistry.register([], element_type=f"type{i}")
    assert len(ResultRegistry._counters) <= 256
    assert ResultRegistry.register([], element_type="brand_new").startswith("set_elements_")


def test_registry_is_safe_under_concurrent_registration():
    ResultRegistry.set_capacity(20)

    def worker():
        for _ in range(200):
            set_id = ResultRegistry.register([{"elementId": {"guid": "g"}}], element_type="Wall")
            ResultRegistry.has(set_id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(STORAGE_SETS) == 20
    assert ResultRegistry._counters["walls"] == 1600


# --- Ghost elements and undo integrity ----------------------------------------


@pytest.mark.asyncio
async def test_deleted_element_in_set_is_itemized_not_raised(fake_archicad):
    set_id = ResultRegistry.register(
        [{"elementId": {"guid": ALIVE}}, {"elementId": {"guid": GHOST}}], element_type="Wall"
    )
    fake_archicad.on_tapir_command(
        "SetDetailsOfElements",
        {
            "executionResults": [
                {"success": True},
                {"success": False, "error": {"code": -2130312312, "message": "Element not found."}},
            ]
        },
    )

    # Looking up the failed element's Element ID finds it deleted, so it gets none.
    fake_archicad.on_tapir_command(
        "GetDetailsOfElements", {"detailsOfElements": [{"error": {"code": -2130313115, "message": "gone"}}]}
    )

    # The check before a change finds the deleted element; once the user confirms, Archicad still fails
    # it, and that failure is itemized instead of raised.
    result = await archicad_mutate(
        set_id, "set_details", {"details": {"floorIndex": 0, "layerIndex": 2}}, port=fake_archicad.port, confirm=True
    )

    assert result["failedCount"] == 1
    assert result["failedElements"] == [
        {"error": {"code": -2130312312, "message": "Element not found."}, "elementId": {"guid": GHOST}}
    ]
    assert len(result["executionResults"]) == 2


@pytest.mark.asyncio
async def test_malformed_set_entries_are_skipped_and_reported(fake_archicad):
    set_id = ResultRegistry.register([{"elementId": {"guid": "ok"}}, {"elementId": {}}], element_type="Wall")
    fake_archicad.on_tapir_command("DeleteElements", {"executionResults": [{"success": True}]})

    result = await archicad_mutate(set_id, "delete", port=fake_archicad.port)

    assert fake_archicad.calls[-1][1] == {"elements": [{"elementId": {"guid": "ok"}}]}
    assert result["failedElements"] == [{"index": 1, "error": {"message": "Entry has no element GUID; skipped."}}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "payload", "command"),
    [
        ("delete", None, "DeleteElements"),
        ("move", {"vector": {"x": 1, "y": 0, "z": 0}}, "MoveElements"),
        ("rotate", {"rotation": ROTATION}, "RotateElements"),
        ("set_details", {"details": {"floorIndex": 0, "layerIndex": 1}}, "SetDetailsOfElements"),
        ("set_property", {"propertyId": {"guid": "p"}, "propertyValue": {"value": 0}}, "SetPropertyValuesOfElements"),
    ],
)
async def test_each_mutation_is_one_tapir_command(fake_archicad, action, payload, command):
    """Tapir wraps one command in one undoable operation, so a batch must be one call."""
    set_id = ResultRegistry.register([{"elementId": {"guid": _guid(i)}} for i in range(25)], element_type="Wall")
    fake_archicad.on_tapir_command(command, {"executionResults": [{"success": True}] * 25})
    # Every change first checks, read-only, that the elements exist and may be changed, and a delete
    # also looks for doors and windows hosted in them.
    fake_archicad.on_tapir_command("FilterElements", lambda params: {"elements": params["elements"]})
    fake_archicad.on_tapir_command("GetConnectedElements", {"connectedElements": [{"elements": []}] * 25})

    await archicad_mutate(set_id, action, payload, port=fake_archicad.port)

    assert [call[0] for call in fake_archicad.calls if not is_read_only_command(call[0])] == [command]


@pytest.mark.asyncio
async def test_falsy_property_values_are_sent_unchanged(fake_archicad):
    set_id = ResultRegistry.register([{"elementId": {"guid": "g"}}], element_type="Wall")
    fake_archicad.on_tapir_command("SetPropertyValuesOfElements", {"executionResults": [{"success": True}] * 2})

    await archicad_mutate(
        set_id,
        "set_property",
        {
            "propertyValues": [
                {"propertyId": {"guid": "p1"}, "propertyValue": 0},
                {"propertyId": {"guid": "p2"}, "propertyValue": False},
            ]
        },
        port=fake_archicad.port,
    )

    sent = fake_archicad.calls[-1][1]["elementPropertyValues"]
    assert [item["propertyValue"] for item in sent] == [0, False]


@pytest.mark.asyncio
async def test_non_numeric_move_vector_is_a_tool_error(fake_archicad):
    set_id = ResultRegistry.register([{"elementId": {"guid": "g"}}], element_type="Wall")
    with pytest.raises(ToolError, match="must be numbers"):
        await archicad_mutate(set_id, "move", {"vector": {"x": "far"}}, port=fake_archicad.port)
    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_query_returns_running_job_instead_of_empty_set(monkeypatch):
    async def running(name, arguments):
        return {"status": "running", "jobHandle": "job:abc"}

    monkeypatch.setattr(meta_verbs, "call_tool_payload", running)
    assert await archicad_query("Wall") == {"status": "running", "jobHandle": "job:abc"}
    assert len(STORAGE_SETS) == 0


async def _open_project(port: int) -> SetProject:
    return SetProject(port, "solo:/project.pln")


@pytest.mark.asyncio
async def test_query_ids_output_is_capped(monkeypatch):
    elements = [{"elementId": {"guid": str(i)}} for i in range(meta_verbs.MAX_INLINE_ELEMENTS + 5)]

    async def selected(name, arguments):
        return {"elements": elements}

    monkeypatch.setattr(meta_verbs, "call_tool_payload", selected)
    monkeypatch.setattr(meta_verbs, "open_project", _open_project)
    result = await archicad_query("selected", output="ids")
    assert result["count"] == len(elements)
    assert len(result["elements"]) == meta_verbs.MAX_INLINE_ELEMENTS
    assert result["truncated"] is True


# --- Error sanitization and field pruning ---------------------------------------


def test_projection_ignores_missing_fields_and_keeps_element_errors():
    payload = {
        "detailsOfElements": [
            {"type": "Wall", "floorIndex": 0, "details": {"polygonOutline": [[0, 0]] * 500}, "memo": "x"},
            {"error": {"code": -1, "message": "Element not found."}},
        ]
    }
    pruned = prune_payload(payload, ["type", "doesNotExist", "storyIndex"])
    assert pruned["detailsOfElements"] == [
        {"type": "Wall", "storyIndex": 0},
        {"error": {"code": -1, "message": "Element not found."}},
    ]


# --- Tapir HTTP mock (http://127.0.0.1:19723) -----------------------------------


@pytest.fixture
def http_archicad(tapir_http):
    from caracara_archicad_mcp.tools.registration import register_all_tools

    register_all_tools()
    header = SimpleNamespace(core=CoreCommands(Port(19723)), port=Port(19723))
    headers = {Port(19723): header}
    multi_conn_instance.set(SimpleNamespace(active=headers, open_port_headers=headers))
    yield tapir_http
    multi_conn_instance.clear()


@pytest.mark.asyncio
async def test_http_mock_serves_queries_over_loopback(http_archicad):
    http_archicad.on_tapir_command("GetElementsByType", {"elements": [{"elementId": {"guid": "w1"}}]})
    http_archicad.on_tapir_command("GetProjectInfo", {"isUntitled": True, "isTeamwork": False})

    result = await archicad_query("walls", port=19723)

    assert result == {"setId": "set_walls_01", "count": 1, "elementType": "Wall"}
    url, payload = http_archicad.requests[0]
    assert url == "http://127.0.0.1:19723"
    assert payload["command"] == "API.ExecuteAddOnCommand"


@pytest.mark.asyncio
async def test_http_mock_mutation_with_ghost_element(http_archicad):
    set_id = ResultRegistry.register([{"elementId": {"guid": "gone"}}], element_type="Wall")
    http_archicad.on_tapir_command(
        "DeleteElements",
        {
            "executionResults": [
                {"success": False, "error": {"code": 1, "message": "Element does not exist."}},
            ]
        },
    )

    result = await archicad_mutate(set_id, "delete", port=19723)

    assert result["failedElements"][0]["elementId"] == {"guid": "gone"}


@pytest.mark.asyncio
async def test_http_mock_tapir_error_becomes_job_failure_not_crash(http_archicad):
    http_archicad.on_tapir_command("GetSelectedElements", error={"code": 3, "message": "No project open."})

    with pytest.raises(ToolError, match="No project open"):
        await archicad_call_tool("elements_get_selected_elements", {"port": 19723})


# --- Regressions found against a live Archicad -----------------------------------


@pytest.mark.asyncio
async def test_query_all_follows_every_page(monkeypatch):
    pages = {
        None: {"elements": [{"elementId": {"guid": f"a{i}"}} for i in range(100)], "next_page_token": "t:100"},
        "t:100": {"elements": [{"elementId": {"guid": f"b{i}"}} for i in range(100)], "next_page_token": "t:200"},
        "t:200": {"elements": [{"elementId": {"guid": "c"}}]},
    }

    async def paged(name, arguments):
        return pages[arguments.get("page_token")]

    monkeypatch.setattr(meta_verbs, "call_tool_payload", paged)
    monkeypatch.setattr(meta_verbs, "open_project", _open_project)
    result = await archicad_query("all")
    assert result["count"] == 201
    assert ResultRegistry.count(result["setId"]) == 201


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("colors", "expected"),
    [
        (None, [overrides.DEFAULT_HIGHLIGHT_COLOR] * 2),
        ([[255, 0, 0, 128]], [[255, 0, 0, 128]] * 2),
        ([[1, 1, 1, 1], [2, 2, 2, 2]], [[1, 1, 1, 1], [2, 2, 2, 2]]),
    ],
)
async def test_highlight_sends_one_color_per_element(fake_archicad, colors, expected):
    fake_archicad.on_tapir_command("HighlightElements", {"success": True})
    params = {"elements": [{"elementId": {"guid": ALIVE}}, {"elementId": {"guid": GHOST}}]}
    if colors is not None:
        params["highlightedColors"] = colors
    await archicad_call_tool("elements_highlight_elements", {"port": fake_archicad.port, "params": params})
    assert fake_archicad.calls[-1][1]["highlightedColors"] == expected


@pytest.mark.asyncio
async def test_clearing_highlights_still_sends_required_colors_field(fake_archicad):
    fake_archicad.on_tapir_command("HighlightElements", {"success": True})
    await archicad_call_tool("elements_highlight_elements", {"port": fake_archicad.port, "params": {"elements": []}})
    assert fake_archicad.calls[-1][1] == {"elements": [], "highlightedColors": []}


@pytest.mark.asyncio
async def test_highlight_color_count_mismatch_is_a_tool_error(fake_archicad):
    params = {"elements": [{"elementId": {"guid": ALIVE}}], "highlightedColors": [[1, 1, 1, 1], [2, 2, 2, 2]]}
    with pytest.raises(ToolError, match="2 colors for 1 elements"):
        await archicad_call_tool("elements_highlight_elements", {"port": fake_archicad.port, "params": params})
    assert fake_archicad.calls == []


def test_confined_path_creates_missing_parent_folders(output_root):
    resolved = Path(file_paths.confine_path("exports/nested/window.png"))
    assert resolved.parent.is_dir()
    assert resolved.parent.is_relative_to(output_root)


@pytest.mark.asyncio
async def test_invalid_arguments_are_logged_without_their_values(fake_archicad, caplog):
    """REVIEW.md R13: values the model sent can be project data, so errors and the log name only the fields."""
    with caplog.at_level("ERROR"), pytest.raises(ToolError, match=r"params\.elementType") as raised:
        await archicad_call_tool(
            "elements_get_elements_by_type",
            {"port": fake_archicad.port, "params": {"elementType": "CONFIDENTIAL-CLIENT"}},
        )

    assert "CONFIDENTIAL-CLIENT" not in str(raised.value)
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "params.elementType" in logged
    assert "CONFIDENTIAL-CLIENT" not in logged
