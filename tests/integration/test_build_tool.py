"""archicad_build: a list of new elements, checked as a whole before anything is made."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad.errors import TapirCommandError

from caracara_archicad_mcp import safety
from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom.build import archicad_build

GUIDS = [f"00000000-0000-0000-0000-0000000001{i:02d}" for i in range(20)]


@pytest.fixture(autouse=True)
def clean_state():
    ResultRegistry.clear()
    safety.set_read_only(None)
    yield
    ResultRegistry.clear()
    safety.set_read_only(None)


@pytest.fixture
def archicad(fake_archicad):
    """Archicad that makes every element it is given, numbering the GUIDs across commands."""
    made = iter(GUIDS)

    def create(list_field):
        return lambda params: {"elements": [{"elementId": {"guid": next(made)}} for _ in params[list_field]]}

    fake_archicad.on_tapir_command("CreateWalls", create("wallsData"))
    fake_archicad.on_tapir_command("CreateSlabs", create("slabsData"))
    fake_archicad.on_tapir_command("CreateDoors", create("doorsData"))
    fake_archicad.on_tapir_command(
        "GetStories",
        {
            "firstStory": 0,
            "lastStory": 1,
            "actStory": 0,
            "skipNullFloor": False,
            "stories": [
                {"index": 0, "floorId": 1, "dispOnSections": True, "level": 0.0, "height": 3.0, "name": "Ground"},
                {"index": 1, "floorId": 2, "dispOnSections": True, "level": 3.0, "height": 3.0, "name": "First"},
            ],
        },
    )
    return fake_archicad


def _wall(ref, x1, y1, x2, y2, **fields):
    return {
        "type": "Wall",
        "ref": ref,
        "begCoordinate": {"x": x1, "y": y1},
        "endCoordinate": {"x": x2, "y": y2},
        **fields,
    }


ROOM = [
    _wall("south", 0, 0, 6, 0, floorIndex=0, thickness=0.3),
    _wall("east", 6, 0, 6, 4, floorIndex=0, thickness=0.3),
    {"type": "door", "hostRef": "south", "centerOffset": 1.5},
]


def _created(fake):
    return [command for command, _ in fake.calls if command.startswith("Create")]


@pytest.mark.asyncio
async def test_builds_walls_and_puts_the_door_in_its_wall(archicad):
    result = await archicad_build(ROOM, port=archicad.port)

    assert result["createdCount"] == 3
    assert result["created"] == {"Wall": 2, "Door": 1}
    assert result["undoSteps"] == 2
    assert result["refs"] == {"south": {"guid": GUIDS[0]}, "east": {"guid": GUIDS[1]}}
    assert ResultRegistry.get(result["setId"]) == [{"elementId": {"guid": guid}} for guid in GUIDS[:3]]
    assert "archicad_mutate" in result["message"]
    doors = dict(archicad.calls)["CreateDoors"]["doorsData"]
    assert doors == [{"ownerWallId": {"guid": GUIDS[0]}, "centerOffset": 1.5}]


@pytest.mark.asyncio
async def test_dry_run_reports_the_plan_and_creates_nothing(archicad):
    result = await archicad_build(ROOM, port=archicad.port, dry_run=True)

    assert result == {
        "dryRun": True,
        "count": 3,
        "byType": {"Wall": 2, "Door": 1},
        "undoSteps": 2,
        "floorIndices": [0],
        "extent": {"minX": 0.0, "minY": 0.0, "maxX": 6.0, "maxY": 4.0, "width": 6.0, "depth": 4.0},
    }
    assert _created(archicad) == []


@pytest.mark.asyncio
async def test_a_wrong_field_stops_the_build_before_anything_is_made(archicad):
    slab_without_outline = {"type": "Slab", "level": 0.0}

    with pytest.raises(ToolError, match=r"slabs \(elements\[3\] onwards\) do not match CreateSlabs"):
        await archicad_build([*ROOM, slab_without_outline], port=archicad.port)

    assert _created(archicad) == []


@pytest.mark.asyncio
async def test_a_story_that_does_not_exist_stops_the_build(archicad):
    with pytest.raises(ToolError, match="No story with floorIndex 5; the project has 0, 1"):
        await archicad_build([_wall("w", 0, 0, 1, 0, floorIndex=5)], port=archicad.port)

    assert _created(archicad) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("elements", "message"),
    [
        ([], "elements is empty"),
        ([{"type": "Stair"}], "unknown type 'Stair'"),
        ([_wall("w", 0, 0, 1, 0), _wall("w", 1, 0, 2, 0)], "repeated: w"),
        ([{"type": "Door", "hostRef": "nowhere", "centerOffset": 1.0}], "not the ref of a wall in this build"),
        ([_wall("w", 0, 0, 1, 0), {"type": "Slab", "hostRef": "w"}], "only doors and windows take a 'hostRef'"),
    ],
)
async def test_mistakes_in_the_list_are_reported_before_anything_is_made(archicad, elements, message):
    with pytest.raises(ToolError, match=message):
        await archicad_build(elements, port=archicad.port)

    assert _created(archicad) == []


@pytest.mark.asyncio
async def test_a_door_whose_wall_failed_is_listed_and_not_sent(archicad):
    archicad.on_tapir_command(
        "CreateWalls",
        {"elements": [{"error": {"code": 1, "message": "zero length"}}, {"elementId": {"guid": GUIDS[0]}}]},
    )

    result = await archicad_build(ROOM, port=archicad.port)

    assert result["created"] == {"Wall": 1}
    assert result["failedElements"] == [
        {"index": 0, "type": "Wall", "error": {"code": 1, "message": "zero length"}, "ref": "south"},
        {"index": 2, "type": "Door", "error": {"message": "its wall 'south' was not created"}},
    ]
    assert result["undoSteps"] == 1
    assert "CreateDoors" not in _created(archicad)


@pytest.mark.asyncio
async def test_what_was_made_before_archicad_stopped_comes_back_as_a_set(archicad):
    def refuse(params):
        raise TapirCommandError("library part missing", code=1)

    archicad.on_tapir_command("CreateDoors", refuse)

    result = await archicad_build(ROOM, port=archicad.port)

    assert result["complete"] is False
    assert "library part missing" in result["error"]
    assert result["created"] == {"Wall": 2}
    assert result["undoSteps"] == 1
    assert len(ResultRegistry.get(result["setId"])) == 2


@pytest.mark.asyncio
async def test_read_only_mode_refuses_the_build_but_allows_the_dry_run(archicad):
    safety.set_read_only(True)

    with pytest.raises(ToolError, match="read-only mode"):
        await archicad_build(ROOM, port=archicad.port)
    assert (await archicad_build(ROOM, port=archicad.port, dry_run=True))["dryRun"] is True

    assert _created(archicad) == []
