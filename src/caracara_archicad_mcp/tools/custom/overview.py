"""archicad_project_overview: the project facts an agent needs before working, in one read-only call.

Instead of spending several tool calls on "which stories, layers and units are there?", the
agent asks once. Details that identify a person or a site (file path, geographic
coordinates, the Project Info fields such as client and address) are left out unless the
caller asks for them with include_private=true.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.tools.custom.functions import call_and_collect, call_and_wait, ready_instance

MAX_LISTED_LAYERS = 500


async def _field(name: str, arguments: dict[str, Any], field: str) -> Any:
    result = await call_and_wait(name, arguments)
    if not isinstance(result, dict) or field not in result:
        raise ToolError(f"Archicad returned no '{field}'")
    return result[field]


async def _stories(port: int) -> list[dict[str, Any]]:
    stories = await call_and_collect("project_get_stories", {"port": port}, "stories")
    return [
        {key: story.get(key) for key in ("index", "name", "level", "height") if story.get(key) is not None}
        for story in stories
    ]


async def _units(port: int) -> dict[str, Any]:
    result = await call_and_wait("project_get_calculation_units", {"port": port})
    if not isinstance(result, dict) or "length" not in result:
        raise ToolError("Archicad returned no calculation units")
    return {kind: result[kind] for kind in ("length", "area", "volume", "angle") if kind in result}


async def _layers(port: int) -> dict[str, Any]:
    headers = await call_and_collect(
        "attributes_get_attributes_by_type", {"port": port, "params": {"attributeType": "Layer"}}, "attributes"
    )
    details = await call_and_collect(
        "attributes_get_layer_attributes",
        {"port": port, "params": {"attributeIds": [{"attributeId": header["attributeId"]} for header in headers]}},
        "attributes",
    )
    layers = [
        item["layerAttribute"]
        for item in details
        if isinstance(item, dict) and isinstance(item.get("layerAttribute"), dict) and "name" in item["layerAttribute"]
    ]
    names = [layer["name"] for layer in layers]
    summary: dict[str, Any] = {
        "count": len(names),
        "names": names[:MAX_LISTED_LAYERS],
        "locked": [layer["name"] for layer in layers if layer.get("isLocked")],
        "hidden": [layer["name"] for layer in layers if layer.get("isHidden")],
    }
    if len(names) > MAX_LISTED_LAYERS:
        summary["truncated"] = True
    return summary


async def _classification_systems(port: int) -> list[dict[str, Any]]:
    systems = await call_and_collect(
        "classifications_get_all_classification_systems", {"port": port}, "classificationSystems"
    )
    return [{"name": system.get("name"), "version": system.get("version")} for system in systems]


async def _geo_location(port: int) -> dict[str, Any]:
    location = await _field("project_get_geo_location", {"port": port}, "projectLocation")
    return location if isinstance(location, dict) else {}


async def _project_info(port: int) -> dict[str, str]:
    fields = await call_and_collect("project_get_project_info_fields", {"port": port}, "fields")
    return {field["projectInfoName"]: field["projectInfoValue"] for field in fields if field.get("projectInfoValue")}


@mcp.tool(
    name="archicad_project_overview",
    title="Archicad Project Overview",
    description=(
        "Returns the facts needed before working on a project, in one call: Archicad and Tapir versions, "
        "project name and type, stories (index, name, level, height), calculation units, north direction, "
        "layers (names, and which are locked or hidden) and classification systems. "
        "include_private=true also returns the project's file path, its geographic location and the "
        "Project Info fields (client, address, ...); leave it off unless the task needs them."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_project_overview(port: int | None = None, include_private: bool = False) -> dict[str, Any]:
    target_port = port or 19723
    instance = ready_instance(target_port).model_dump(by_alias=True)
    overview: dict[str, Any] = {
        "project": {
            "name": instance["projectName"],
            "type": instance["projectType"],
            "archicadVersion": instance["archicadVersion"],
            "tapirVersion": instance["tapirVersion"],
        }
    }
    if include_private and instance["projectPath"]:
        overview["project"]["path"] = instance["projectPath"]

    warnings: list[str] = []
    sections = {
        "stories": _stories,
        "units": _units,
        "layers": _layers,
        "classificationSystems": _classification_systems,
    }
    for section, read in sections.items():
        try:
            overview[section] = await read(target_port)
        except (ToolError, KeyError, TypeError) as error:
            warnings.append(f"{section} unavailable: {error}")

    try:
        location = await _geo_location(target_port)
        if isinstance(location.get("north"), int | float):
            overview["northDegrees"] = round(math.degrees(location["north"]), 2)
        if include_private:
            overview["location"] = {
                key: location[key] for key in ("latitude", "longitude", "altitude") if key in location
            }
    except (ToolError, KeyError, TypeError) as error:
        warnings.append(f"location unavailable: {error}")

    if include_private:
        try:
            overview["projectInfo"] = await _project_info(target_port)
        except (ToolError, KeyError, TypeError) as error:
            warnings.append(f"projectInfo unavailable: {error}")

    if warnings:
        overview["warnings"] = warnings
    return overview
