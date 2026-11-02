"""archicad_spatial_query end to end: boxes from Archicad in, related element sets out."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom.spatial_query import archicad_spatial_query


def _guid(i: int) -> str:
    return f"00000000-0000-0000-0000-{i:012d}"


def _ids(*numbers: int) -> list[dict]:
    return [{"elementId": {"guid": _guid(n)}} for n in numbers]


# Element number -> bounding box (xMin, yMin, zMin, xMax, yMax, zMax); None for a 2D-only element.
BOXES = {
    1: (0, 0, 0, 5, 0.3, 3),  # wall
    2: (0, 0.5, 2.5, 5, 0.6, 2.6),  # conduit 0.2 m from the wall
    3: (0, 3, 2.5, 5, 3.1, 2.6),  # conduit 2.7 m from the wall
    10: (0, 0, 0, 4, 4, 3),  # zone A
    11: (6, 0, 0, 10, 4, 3),  # zone B
    20: (1, 1, 0, 2, 2, 0.8),  # table in zone A
    21: (7, 1, 0, 8, 2, 0.8),  # table in zone B
    22: (20, 20, 0, 21, 21, 0.8),  # table outside
    30: None,  # a label
}


@pytest.fixture(autouse=True)
def archicad(fake_archicad):
    ResultRegistry.clear()

    def boxes(params):
        out = []
        for item in params["elements"]:
            number = int(item["elementId"]["guid"][-12:])
            box = BOXES[number]
            if box is None:
                out.append({"error": {"code": 1, "message": "no 3D model"}})
            else:
                out.append(
                    {"boundingBox3D": dict(zip(["xMin", "yMin", "zMin", "xMax", "yMax", "zMax"], box, strict=True))}
                )
        return {"boundingBoxes3D": out}

    def element_ids(params):
        return {"detailsOfElements": [{"id": f"E-{int(i['elementId']['guid'][-12:])}"} for i in params["elements"]]}

    fake_archicad.on_tapir_command("Get3DBoundingBoxes", boxes)
    fake_archicad.on_tapir_command("GetDetailsOfElements", element_ids)
    yield fake_archicad
    ResultRegistry.clear()


@pytest.mark.asyncio
async def test_near_finds_conduits_close_to_walls(archicad):
    result = await archicad_spatial_query("near", _ids(2, 3), _ids(1), distance=0.5, port=archicad.port)

    assert result["pairCount"] == 1
    assert result["matchedCount"] == 1
    assert ResultRegistry.get(result["matchedSetId"]) == _ids(2)
    assert ResultRegistry.get(result["otherMatchedSetId"]) == _ids(1)
    assert result["nearest"] == [
        {"element": {"guid": _guid(2), "id": "E-2"}, "other": {"guid": _guid(1), "id": "E-1"}, "gapMeters": 0.2}
    ]


@pytest.mark.asyncio
async def test_near_with_nothing_close(archicad):
    result = await archicad_spatial_query("near", _ids(3), _ids(1), distance=0.1, port=archicad.port)
    assert result["pairCount"] == 0
    assert result["matchedSetId"] is None


@pytest.mark.asyncio
async def test_inside_counts_furniture_per_zone(archicad):
    result = await archicad_spatial_query("inside", _ids(20, 21, 22, 30), _ids(10, 11), port=archicad.port)

    assert result["insideCount"] == 2
    assert result["outsideCount"] == 1
    assert ResultRegistry.get(result["insideSetId"]) == _ids(20, 21)
    assert sorted(item["container"]["guid"] for item in result["byContainer"]) == [_guid(10), _guid(11)]
    assert sorted(item["container"]["id"] for item in result["byContainer"]) == ["E-10", "E-11"]
    assert result["withoutBoxCount"] == 1


@pytest.mark.asyncio
async def test_sets_can_be_given_by_id(archicad):
    tables = ResultRegistry.register(_ids(20, 21, 22), element_type="Object")
    zones = ResultRegistry.register(_ids(10), element_type="Zone")

    result = await archicad_spatial_query("inside", tables, zones, port=archicad.port)

    assert result["insideCount"] == 1


@pytest.mark.asyncio
async def test_collides_uses_archicads_exact_check(archicad):
    archicad.on_tapir_command(
        "GetCollisions",
        {
            "collisions": [
                {
                    "elementId1": {"guid": _guid(2)},
                    "elementId2": {"guid": _guid(1)},
                    "hasBodyCollision": True,
                    "hasClearenceCollision": False,
                }
            ]
        },
    )

    result = await archicad_spatial_query("collides", _ids(2, 3), _ids(1), port=archicad.port)

    assert ("GetCollisions", {"elementsGroup1": _ids(2, 3), "elementsGroup2": _ids(1)}) in archicad.calls
    assert result["examples"][0]["element"] == {"guid": _guid(2), "id": "E-2"}
    assert result["collisionCount"] == 1
    assert result["bodyCollisionCount"] == 1
    assert ResultRegistry.get(result["matchedSetId"]) == _ids(2)


@pytest.mark.asyncio
async def test_only_reads_reach_archicad(archicad):
    await archicad_spatial_query("near", _ids(1, 2, 3), _ids(1, 2, 3), distance=1.0, port=archicad.port)
    assert {command for command, _ in archicad.calls} == {
        "GetProjectInfo",
        "Get3DBoundingBoxes",
        "GetDetailsOfElements",
    }


@pytest.mark.asyncio
async def test_bad_input_is_refused(archicad):
    with pytest.raises(ToolError, match="zero or more"):
        await archicad_spatial_query("near", _ids(1), _ids(2), distance=-1, port=archicad.port)
    with pytest.raises(ToolError, match="no elements"):
        await archicad_spatial_query("near", [], _ids(2), port=archicad.port)
    with pytest.raises(ToolError, match="not found"):
        await archicad_spatial_query("near", "set_missing_01", _ids(2), port=archicad.port)


@pytest.mark.asyncio
async def test_unread_element_ids_leave_the_answer_usable(archicad):
    archicad.on_tapir_command("GetDetailsOfElements", {"error": {"code": 1, "message": "unavailable"}})

    result = await archicad_spatial_query("near", _ids(2), _ids(1), distance=0.5, port=archicad.port)

    assert result["nearest"] == [{"element": {"guid": _guid(2)}, "other": {"guid": _guid(1)}, "gapMeters": 0.2}]
    assert result["warnings"][0].startswith("Element IDs were not read")
