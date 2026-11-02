"""archicad_find_properties end to end: every definition read from Archicad, the closest few returned."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp import settings
from caracara_archicad_mcp.tools.custom.find_properties import archicad_find_properties

pytestmark = pytest.mark.asyncio


def definition(number: int, name: str, group: str, kind: str = "StaticBuiltIn") -> dict:
    return {
        "propertyId": {"guid": f"00000000-0000-0000-0000-{number:012d}"},
        "propertyType": kind,
        "propertyGroupName": group,
        "propertyName": name,
        "propertyCollectionType": "Single",
        "propertyValueType": "String",
        "propertyMeasureType": "Default",
        "propertyIsEditable": kind == "Custom",
        "isExpressionBased": False,
    }


DEFINITIONS = [
    definition(1, "Element ID", "General"),
    definition(2, "Net Volume", "Geometry"),
    definition(3, "Fire Rating", "Fire safety", "Custom"),
    definition(4, "Fire Resistance Rating", "Fire safety", "Custom"),
    definition(5, "Cost Code", "Cost", "Custom"),
]


@pytest.fixture(autouse=True)
def archicad(fake_archicad):
    settings.set_read_only(None)
    fake_archicad.on_tapir_command("GetAllProperties", {"properties": DEFINITIONS})
    yield fake_archicad
    settings.set_read_only(None)


async def test_finds_a_property_by_the_name_the_user_uses(archicad):
    result = await archicad_find_properties("fire raitng", port=archicad.port)

    assert [match["name"] for match in result["properties"]] == ["Fire Rating", "Fire Resistance Rating"]
    assert result["properties"][0] == {
        "propertyId": {"guid": "00000000-0000-0000-0000-000000000003"},
        "name": "Fire Rating",
        "group": "Fire safety",
        "type": "Custom",
        "valueType": "String",
        "collectionType": "Single",
        "measureType": "Default",
        "editable": True,
        "score": result["properties"][0]["score"],
    }
    assert result["matchCount"] == 2
    assert result["searchedCount"] == 5


async def test_the_best_match_comes_first_and_limit_caps_the_list(archicad):
    result = await archicad_find_properties("Fire Rating", limit=1, port=archicad.port)

    assert [match["name"] for match in result["properties"]] == ["Fire Rating"]
    assert result["properties"][0]["score"] == 1.0
    assert result["matchCount"] == 2


async def test_nothing_close_returns_an_empty_list(archicad):
    result = await archicad_find_properties("thermal transmittance", port=archicad.port)
    assert result["properties"] == []
    assert result["matchCount"] == 0


async def test_it_reads_definitions_only_and_works_in_read_only_mode(archicad):
    settings.set_read_only(True)

    await archicad_find_properties("cost code", port=archicad.port)

    assert [command for command, _ in archicad.calls] == ["GetAllProperties"]


async def test_bad_input_is_refused_before_archicad_is_asked(archicad):
    with pytest.raises(ToolError, match="must contain a word"):
        await archicad_find_properties(" - ", port=archicad.port)
    with pytest.raises(ToolError, match="between 1 and 50"):
        await archicad_find_properties("fire", limit=0, port=archicad.port)
    assert archicad.calls == []
