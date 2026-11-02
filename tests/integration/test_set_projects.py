"""A set is used only in the project, and on the port, where its elements were found."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp.result_registry import ResultRegistry, SetProject
from caracara_archicad_mcp.tools.custom.calculate import archicad_calculate
from caracara_archicad_mcp.tools.custom.functions import call_tool_payload
from caracara_archicad_mcp.tools.custom.meta_verbs import archicad_mutate, archicad_query
from caracara_archicad_mcp.tools.custom.spatial_query import archicad_spatial_query

WALLS = [{"elementId": {"guid": "00000000-0000-0000-0000-000000000001"}}]
COPY = {
    "isUntitled": False,
    "isTeamwork": False,
    "projectLocation": "/path/to/copy_of_project.pln",
    "projectPath": "/path/to/copy_of_project.pln",
    "projectName": "copy_of_project",
}


@pytest.fixture(autouse=True)
def clean_registry():
    ResultRegistry.clear()
    yield
    ResultRegistry.clear()


@pytest.fixture
def walls_then_copy_opened(fake_archicad):
    """A set of walls found in the project, after which the user opens a copy of it, which has the same GUIDs."""
    fake_archicad.on_tapir_command("GetElementsByType", {"elements": WALLS})
    fake_archicad.on_tapir_command("FilterElements", lambda params: {"elements": params["elements"]})

    async def open_copy() -> str:
        set_id = (await archicad_query("Wall", port=fake_archicad.port))["setId"]
        fake_archicad.on_tapir_command("GetProjectInfo", COPY)
        fake_archicad.calls.clear()
        return set_id

    return open_copy


@pytest.mark.asyncio
async def test_a_set_keeps_working_in_its_own_project(fake_archicad):
    fake_archicad.on_tapir_command("GetElementsByType", {"elements": WALLS})
    fake_archicad.on_tapir_command("FilterElements", lambda params: {"elements": params["elements"]})
    fake_archicad.on_tapir_command("DeleteElements", {"executionResults": [{"success": True}]})
    set_id = (await archicad_query("Wall", port=fake_archicad.port))["setId"]

    result = await archicad_mutate(set_id, "delete", port=fake_archicad.port, confirm=True)

    assert result["executionResults"] == [{"success": True}]
    assert ResultRegistry.project_of(set_id) == SetProject(fake_archicad.port, "solo:/path/to/fake_project.pln")


@pytest.mark.asyncio
async def test_a_change_with_a_set_from_another_project_is_refused(fake_archicad, walls_then_copy_opened):
    set_id = await walls_then_copy_opened()

    with pytest.raises(ToolError, match=r"was found in /path/to/fake_project.pln on port 19723.*copy_of_project"):
        await archicad_mutate(set_id, "delete", port=fake_archicad.port, confirm=True)

    assert [command for command, _ in fake_archicad.calls] == ["GetProjectInfo"]


@pytest.mark.asyncio
async def test_reads_with_a_set_from_another_project_are_refused(fake_archicad, walls_then_copy_opened):
    set_id = await walls_then_copy_opened()

    with pytest.raises(ToolError, match="query again in this one"):
        await archicad_calculate(set_id, port=fake_archicad.port)
    with pytest.raises(ToolError, match="query again in this one"):
        await archicad_spatial_query("near", set_id, WALLS, port=fake_archicad.port)


@pytest.mark.asyncio
async def test_archicad_call_tool_refuses_a_set_from_another_project(fake_archicad, walls_then_copy_opened):
    set_id = await walls_then_copy_opened()

    with pytest.raises(ToolError, match="query again in this one"):
        await call_tool_payload("elements_delete_elements", {"port": fake_archicad.port, "targetSetId": set_id})

    assert "DeleteElements" not in [command for command, _ in fake_archicad.calls]


@pytest.mark.asyncio
async def test_a_set_from_another_archicad_is_refused(fake_archicad):
    same_file_elsewhere = SetProject(19724, "solo:/path/to/fake_project.pln")
    set_id = ResultRegistry.register(WALLS, "Wall", same_file_elsewhere)

    with pytest.raises(ToolError, match=r"on port 19724, but this call works in .* on port 19723"):
        await archicad_mutate(set_id, "highlight", port=fake_archicad.port)


@pytest.mark.asyncio
async def test_sets_made_by_tools_remember_their_project(fake_archicad):
    fake_archicad.on_tapir_command("GetSelectedElements", {"elements": WALLS})
    box = {"xMin": 0, "yMin": 0, "zMin": 0, "xMax": 1, "yMax": 1, "zMax": 1}
    fake_archicad.on_tapir_command(
        "Get3DBoundingBoxes", lambda params: {"boundingBoxes3D": [{"boundingBox3D": box} for _ in params["elements"]]}
    )
    fake_archicad.on_tapir_command(
        "GetDetailsOfElements", lambda params: {"detailsOfElements": [{"id": "W"} for _ in params["elements"]]}
    )
    here = SetProject(fake_archicad.port, "solo:/path/to/fake_project.pln")
    door = [{"elementId": {"guid": "00000000-0000-0000-0000-000000000002"}}]

    selected = (await archicad_query("selected", port=fake_archicad.port))["setId"]
    near = (await archicad_spatial_query("near", WALLS, door, port=fake_archicad.port))["matchedSetId"]

    assert ResultRegistry.project_of(selected) == here
    assert ResultRegistry.project_of(near) == here
