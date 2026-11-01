"""archicad_project_overview: one read-only call, private details only on request."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from caracara_archicad_mcp.tools.custom.overview import archicad_project_overview

LAYER_GUIDS = [f"00000000-0000-0000-0000-0000000009{i:02d}" for i in range(3)]


@pytest.fixture
def project(fake_archicad):
    fake_archicad.tapir_info = SimpleNamespace(version="1.7.2", is_older=False, is_newer=False)
    fake_archicad.on_tapir_command(
        "GetStories",
        {
            "firstStory": 0,
            "lastStory": 1,
            "actStory": 0,
            "skipNullFloor": False,
            "stories": [
                {"index": 0, "floorId": 1, "dispOnSections": True, "level": 0.0, "height": 3.2, "name": "Ground"},
                {"index": 1, "floorId": 2, "dispOnSections": True, "level": 3.2, "height": 3.0, "name": "First"},
            ],
        },
    )
    fake_archicad.on_tapir_command(
        "GetCalculationUnits",
        {
            "length": {"unit": "Meter", "accuracy": "TwoDecimals"},
            "area": {"unit": "SquareMeter", "accuracy": "TwoDecimals"},
            "volume": {"unit": "CubicMeter", "accuracy": "TwoDecimals"},
            "angle": {"unit": "DecimalDegree", "accuracy": "TwoDecimals"},
        },
    )
    fake_archicad.on_tapir_command(
        "GetAttributesByType",
        {
            "attributes": [
                {"attributeId": {"guid": guid}, "index": i + 1, "name": name, "modificationTime": 0}
                for i, (guid, name) in enumerate(zip(LAYER_GUIDS, ["Walls", "Furniture", "Site"], strict=True))
            ]
        },
    )
    fake_archicad.on_tapir_command(
        "API.GetLayerAttributes",
        {
            "attributes": [
                {
                    "layerAttribute": {
                        "attributeId": {"guid": guid},
                        "name": name,
                        "intersectionGroupNr": 0,
                        "isHidden": hidden,
                        "isLocked": locked,
                        "isWireframe": False,
                    }
                }
                for guid, name, hidden, locked in zip(
                    LAYER_GUIDS,
                    ["Walls", "Furniture", "Site"],
                    [False, True, False],
                    [False, False, True],
                    strict=True,
                )
            ]
        },
    )
    fake_archicad.on_tapir_command(
        "API.GetAllClassificationSystems",
        {
            "classificationSystems": [
                {
                    "classificationSystemId": {"guid": "00000000-0000-0000-0000-000000000c1a"},
                    "name": "ARCHICAD Classification",
                    "description": "",
                    "source": "",
                    "version": "v 2.0",
                    "date": "2019-01-01",
                }
            ]
        },
    )
    fake_archicad.on_tapir_command(
        "GetGeoLocation",
        {
            "projectLocation": {"longitude": 19.04, "latitude": 47.5, "altitude": 120.0, "north": math.pi / 2},
            "surveyPoint": {
                "position": {},
                "positionInProject": {"x": 0, "y": 0, "z": 0},
                "geoReferencingParameters": {},
            },
        },
    )
    fake_archicad.on_tapir_command(
        "GetProjectInfoFields",
        {
            "fields": [
                {"projectInfoId": "CLIENT", "projectInfoName": "Client", "projectInfoValue": "Jane Doe"},
                {"projectInfoId": "EMPTY", "projectInfoName": "Notes", "projectInfoValue": ""},
            ]
        },
    )
    return fake_archicad


@pytest.mark.asyncio
async def test_overview_has_what_an_agent_needs(project):
    overview = await archicad_project_overview(port=project.port)

    assert overview["project"] == {
        "name": "FakeProject",
        "type": "solo",
        "archicadVersion": "28",
        "tapirVersion": "1.7.2",
    }
    assert overview["stories"] == [
        {"index": 0, "name": "Ground", "level": 0.0, "height": 3.2},
        {"index": 1, "name": "First", "level": 3.2, "height": 3.0},
    ]
    assert overview["units"]["length"] == {"unit": "Meter", "accuracy": "TwoDecimals"}
    assert overview["layers"] == {
        "count": 3,
        "names": ["Walls", "Furniture", "Site"],
        "locked": ["Site"],
        "hidden": ["Furniture"],
    }
    assert overview["classificationSystems"] == [{"name": "ARCHICAD Classification", "version": "v 2.0"}]
    assert overview["northDegrees"] == 90.0
    assert "warnings" not in overview


@pytest.mark.asyncio
async def test_private_details_are_left_out_by_default(project):
    overview = await archicad_project_overview(port=project.port)

    assert "path" not in overview["project"]
    assert "location" not in overview
    assert "projectInfo" not in overview
    assert "Jane Doe" not in str(overview)
    assert "GetProjectInfoFields" not in [command for command, _ in project.calls]


@pytest.mark.asyncio
async def test_private_details_on_request(project):
    overview = await archicad_project_overview(port=project.port, include_private=True)

    assert overview["project"]["path"] == "/path/to/fake_project.pln"
    assert overview["location"] == {"latitude": 47.5, "longitude": 19.04, "altitude": 120.0}
    assert overview["projectInfo"] == {"Client": "Jane Doe"}


@pytest.mark.asyncio
async def test_a_missing_section_does_not_spoil_the_rest(project):
    project.on_tapir_command("GetCalculationUnits", {"error": {"code": 1, "message": "unavailable"}})

    overview = await archicad_project_overview(port=project.port)

    assert "units" not in overview
    assert overview["stories"]
    assert overview["warnings"] == ["units unavailable: Archicad returned no calculation units"]


@pytest.mark.asyncio
async def test_only_reads_reach_archicad(project):
    await archicad_project_overview(port=project.port, include_private=True)
    assert all(command.removeprefix("API.").startswith("Get") for command, _ in project.calls)
