"""archicad_build: creates new elements from one list, checked as a whole before anything is made.

Archicad has one create command per element type and makes each one its own Undo step, so a
build of walls and doors takes two Ctrl+Z. Everything a build made comes back as one element
set instead, which one archicad_mutate delete removes again. Doors and windows can name a wall
of the same build by its 'ref', because the wall's GUID exists only once Archicad has made it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import ValidationError

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.result_registry import ResultRegistry, SetProject
from caracara_archicad_mcp.safety import ensure_allowed
from caracara_archicad_mcp.tools.custom.element_sets import normalize_element_type, open_project
from caracara_archicad_mcp.tools.custom.functions import call_and_collect, call_and_wait
from caracara_archicad_mcp.tools.custom.mutation_checks import MAX_LISTED
from caracara_archicad_mcp.tools.tool_registry import get_tool_entry
from caracara_archicad_mcp.validation_errors import format_validation_error

# Element type -> (Archicad command, its tool name, the parameter list it takes), in the order a
# build makes them: hosts before the doors and windows placed in them.
ELEMENT_COMMANDS = {
    "Wall": ("CreateWalls", "elements_create_walls", "wallsData"),
    "Column": ("CreateColumns", "elements_create_columns", "columnsData"),
    "Beam": ("CreateBeams", "elements_create_beams", "beamsData"),
    "Slab": ("CreateSlabs", "elements_create_slabs", "slabsData"),
    "Roof": ("CreateRoofs", "elements_create_roofs", "roofsData"),
    "Zone": ("CreateZones", "elements_create_zones", "zonesData"),
    "Door": ("CreateDoors", "elements_create_doors", "doorsData"),
    "Window": ("CreateWindows", "elements_create_windows", "windowsData"),
}
HOSTED_TYPES = ("Door", "Window")
MAX_ELEMENTS = 2000
# Stands in for a wall made earlier in the same build while its door or window is checked.
_PENDING_HOST = {"guid": "00000000-0000-0000-0000-000000000000"}


def _element_type(item: dict[str, Any], index: int) -> str:
    element_type = normalize_element_type(str(item.get("type", "")))
    if element_type in ELEMENT_COMMANDS:
        return element_type
    raise ToolError(
        f"elements[{index}]: unknown type '{item.get('type')}'. Supported: {', '.join(ELEMENT_COMMANDS)}. "
        "Other elements are made with archicad_call_tool."
    )


@dataclass
class _Item:
    """One element of a build: its place in the request, its type, its optional ref and its data."""

    index: int
    element_type: str
    ref: str | None
    host_ref: str | None
    data: dict[str, Any]


def _parse(elements: list[Any]) -> list[_Item]:
    items: list[_Item] = []
    for index, raw in enumerate(elements):
        if not isinstance(raw, dict):
            raise ToolError(f"elements[{index}] must be an object with a 'type'.")
        data = {key: value for key, value in raw.items() if key not in ("type", "ref", "hostRef")}
        ref, host_ref = raw.get("ref"), raw.get("hostRef")
        items.append(_Item(index, _element_type(raw, index), ref, host_ref, data))
    return items


def _check_refs(items: list[_Item]) -> None:
    refs = Counter(item.ref for item in items if item.ref is not None)
    if repeated := [ref for ref, count in refs.items() if count > 1]:
        raise ToolError(f"Each 'ref' must be unique; repeated: {', '.join(map(str, repeated))}.")
    walls = {item.ref for item in items if item.element_type == "Wall"}
    for item in items:
        if item.host_ref is None:
            continue
        if item.element_type not in HOSTED_TYPES:
            raise ToolError(f"elements[{item.index}]: only doors and windows take a 'hostRef'.")
        if item.host_ref not in walls:
            raise ToolError(
                f"elements[{item.index}]: 'hostRef' '{item.host_ref}' is not the ref of a wall in this build."
            )
        if "ownerWallId" in item.data:
            raise ToolError(f"elements[{item.index}]: give either 'hostRef' or 'ownerWallId', not both.")


def _validate(element_type: str, items: list[_Item], port: int) -> None:
    """Checks the items against the create command's own schema, so a wrong field stops the build before it starts."""
    command, tool, list_field = ELEMENT_COMMANDS[element_type]
    data = [{**item.data, "ownerWallId": _PENDING_HOST} if item.host_ref else item.data for item in items]
    try:
        get_tool_entry(tool).arguments_model.model_validate({"port": port, "params": {list_field: data}})
    except ValidationError as error:
        first = items[0].index
        raise ToolError(
            f"The {element_type.lower()}s (elements[{first}] onwards) do not match {command}:\n"
            + format_validation_error(tool, error)
        ) from error


async def _check_stories(items: list[_Item], port: int) -> list[int]:
    wanted = sorted({item.data["floorIndex"] for item in items if isinstance(item.data.get("floorIndex"), int)})
    if not wanted:
        return []
    stories = await call_and_collect("project_get_stories", {"port": port}, "stories")
    existing = {story.get("index") for story in stories}
    if missing := [index for index in wanted if index not in existing]:
        raise ToolError(
            f"No story with floorIndex {', '.join(map(str, missing))}; the project has "
            f"{', '.join(map(str, sorted(i for i in existing if i is not None)))}. Nothing was created."
        )
    return wanted


def _points(value: Any) -> list[tuple[float, float]]:
    """Every 2D coordinate in an element's data, wherever it sits."""
    if isinstance(value, dict):
        if isinstance(value.get("x"), int | float) and isinstance(value.get("y"), int | float):
            return [(float(value["x"]), float(value["y"]))]
        return [point for child in value.values() for point in _points(child)]
    if isinstance(value, list):
        return [point for child in value for point in _points(child)]
    return []


def _extent(items: list[_Item]) -> dict[str, float] | None:
    """The plan size of the build, so a wrong scale (centimetres read as metres) shows before anything is made."""
    points = [point for item in items for point in _points(item.data)]
    if not points:
        return None
    xs, ys = [x for x, _ in points], [y for _, y in points]
    return {
        "minX": min(xs),
        "minY": min(ys),
        "maxX": max(xs),
        "maxY": max(ys),
        "width": round(max(xs) - min(xs), 6),
        "depth": round(max(ys) - min(ys), 6),
    }


async def _plan(elements: list[Any], port: int) -> tuple[dict[str, list[_Item]], dict[str, Any]]:
    """The checked build, grouped by type in the order the types are made, and what the check found."""
    if not elements:
        raise ToolError("elements is empty; give the elements to create.")
    if len(elements) > MAX_ELEMENTS:
        raise ToolError(f"A build makes at most {MAX_ELEMENTS} elements; split it by story or by part of the building.")
    items = _parse(elements)
    _check_refs(items)
    plan = {name: group for name in ELEMENT_COMMANDS if (group := [i for i in items if i.element_type == name])}
    for element_type, group in plan.items():
        _validate(element_type, group, port)
    report: dict[str, Any] = {
        "count": len(items),
        "byType": {name: len(group) for name, group in plan.items()},
        "undoSteps": len(plan),
    }
    if stories := await _check_stories(items, port):
        report["floorIndices"] = stories
    if extent := _extent(items):
        report["extent"] = extent
    return plan, report


@dataclass
class _Built:
    """What a build has made so far, and what Archicad refused."""

    created: list[dict[str, Any]] = field(default_factory=list)
    by_type: Counter[str] = field(default_factory=Counter)
    refs: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed: list[dict[str, Any]] = field(default_factory=list)
    undo_steps: int = 0

    def fail(self, item: _Item, error: Any) -> None:
        entry: dict[str, Any] = {"index": item.index, "type": item.element_type, "error": error}
        if item.ref is not None:
            entry["ref"] = item.ref
        self.failed.append(entry)


def _with_hosts(items: list[_Item], built: _Built) -> list[_Item]:
    """Doors and windows whose wall was made, with its GUID filled in; the others are listed as failed."""
    ready = []
    for item in items:
        if item.host_ref is None:
            ready.append(item)
        elif item.host_ref in built.refs:
            ready.append(replace(item, host_ref=None, data={**item.data, "ownerWallId": built.refs[item.host_ref]}))
        else:
            built.fail(item, {"message": f"its wall '{item.host_ref}' was not created"})
    return ready


async def _create(element_type: str, items: list[_Item], built: _Built, port: int) -> None:
    _, tool, list_field = ELEMENT_COMMANDS[element_type]
    items = _with_hosts(items, built)
    if not items:
        return
    result = await call_and_wait(tool, {"port": port, "params": {list_field: [item.data for item in items]}})
    built.undo_steps += 1
    answers = result.get("elements", []) if isinstance(result, dict) else []
    for position, item in enumerate(items):
        answer = answers[position] if position < len(answers) else {"error": {"message": "Archicad did not answer"}}
        if not isinstance(answer, dict):
            answer = {"error": answer}
        element_id = answer.get("elementId")
        if element_id is None:
            built.fail(item, answer.get("error", answer))
            continue
        built.created.append({"elementId": element_id})
        built.by_type[element_type] += 1
        if item.ref is not None:
            built.refs[item.ref] = element_id


def _result(built: _Built, project: SetProject, error: str | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {
        "createdCount": len(built.created),
        "created": dict(built.by_type),
        "undoSteps": built.undo_steps,
    }
    if built.created:
        response["setId"] = ResultRegistry.register(built.created, "Element", project)
        response["message"] = (
            f"To remove everything this build made, call archicad_mutate(target_set='{response['setId']}', "
            "action='delete'). Ctrl+Z in Archicad would take one step per element type."
        )
    if built.refs:
        response["refs"] = built.refs
    if built.failed:
        response["failedCount"] = len(built.failed)
        response["failedElements"] = built.failed[:MAX_LISTED]
    if error:
        response["complete"] = False
        response["error"] = f"Archicad stopped the build: {error}. What was made before that is in the set."
    return response


@mcp.tool(
    name="archicad_build",
    title="Build Archicad Elements",
    description=(
        "Creates walls, columns, beams, slabs, roofs, zones, doors and windows from one list. "
        "elements: objects with 'type' (Wall, Column, Beam, Slab, Roof, Zone, Door, Window) and the fields of "
        "that type's create command (see archicad_get_command_schema, e.g. elements_create_walls); add 'ref' "
        "to name an element, and give a door or window 'hostRef' to place it in a wall of the same build. "
        "Lengths are in metres. The whole list is checked against the commands' schemas and the project's "
        "stories before anything is made. dry_run=true changes nothing and returns that check with the "
        "build's plan size ('extent'): when the elements come from a picture or drawing, show it to the user "
        "and get a known dimension confirmed before building. The result is one setId of everything made, "
        "ready for archicad_query, archicad_mutate or archicad_capture_view; one archicad_mutate delete of "
        "that set removes the build. Each element type is its own Undo step in Archicad."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def archicad_build(
    elements: list[dict[str, Any]], port: int | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """Create a list of new elements in Archicad."""
    target_port = port or 19723
    plan, report = await _plan(elements, target_port)
    if dry_run:
        return {"dryRun": True, **report}
    for element_type in plan:
        ensure_allowed(ELEMENT_COMMANDS[element_type][0])
    # Read before building, so a failure here cannot hide what was already made.
    project = await open_project(target_port)
    built = _Built()
    for element_type, items in plan.items():
        try:
            await _create(element_type, items, built, target_port)
        except ToolError as error:
            return _result(built, project, str(error))
    return _result(built, project)
