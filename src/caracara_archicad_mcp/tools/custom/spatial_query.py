"""archicad_spatial_query: which elements are near, inside or colliding with which others.

'near' and 'inside' use the elements' 3D bounding boxes from Archicad and a spatial index
(caracara_archicad_mcp.spatial), so thousands of elements are compared in a fraction of a
second. Boxes approximate the shapes, so 'inside' also tests the plan outline of zones and
slabs. 'collides' asks Archicad for an exact check of the real geometry instead
(GetCollisions). The matching elements come back as new element sets, ready for
archicad_query, archicad_calculate or archicad_mutate.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.spatial import Box, Outline, Point2D, containers_of, pairs_within
from caracara_archicad_mcp.tools.custom.element_sets import as_elements, element_ids, labels_or_warning, target_elements
from caracara_archicad_mcp.tools.custom.functions import call_and_wait

MAX_ELEMENTS = 100_000
MAX_LISTED = 20
# Element types whose plan outline is the area they enclose.
OUTLINE_TYPES = ("Zone", "Slab")


def _resolve(target: str | list[Any], label: str) -> list[dict[str, Any]]:
    ids, _ = element_ids(target_elements(target, label))
    if not ids:
        raise ToolError(f"{label} contains no elements.")
    if len(ids) > MAX_ELEMENTS:
        raise ToolError(f"{label} has {len(ids)} elements; use at most {MAX_ELEMENTS}.")
    return ids


async def _boxes(ids: list[dict[str, Any]], port: int) -> tuple[list[Box], int]:
    """The 3D bounding boxes of the elements, and how many elements have none (2D-only elements)."""
    result = await call_and_wait(
        "elements_get3_d_bounding_boxes",
        {"port": port, "params": {"elements": as_elements(ids)}},
    )
    items = result.get("boundingBoxes3D") if isinstance(result, dict) else None
    if not isinstance(items, list) or len(items) != len(ids):
        raise ToolError(f"Archicad returned no bounding boxes: {result}")
    boxes: list[Box] = []
    for elem_id, item in zip(ids, items, strict=True):
        box = item.get("boundingBox3D") if isinstance(item, dict) else None
        if isinstance(box, dict):
            boxes.append(
                Box(elem_id["guid"], box["xMin"], box["yMin"], box["zMin"], box["xMax"], box["yMax"], box["zMax"])
            )
    return boxes, len(ids) - len(boxes)


def _coordinates(points: Any) -> list[Point2D] | None:
    if not isinstance(points, list) or len(points) < 3:
        return None
    try:
        return [(float(point["x"]), float(point["y"])) for point in points]
    except (KeyError, TypeError, ValueError):
        return None


def _outline(entry: Any) -> Outline | None:
    """The plan outline of a zone or slab, with its holes. None for other elements, and for curved outlines,
    whose arcs are not drawn here."""
    if not isinstance(entry, dict) or entry.get("type") not in OUTLINE_TYPES:
        return None
    details = entry.get("details")
    if not isinstance(details, dict) or details.get("polygonArcs"):
        return None
    ring = _coordinates(details.get("polygonOutline"))
    holes: list[list[Point2D]] = []
    for hole in details.get("holes") or []:
        if not isinstance(hole, dict) or hole.get("polygonArcs"):
            return None
        coordinates = _coordinates(hole.get("polygonOutline") or hole.get("polygonCoordinates"))
        if coordinates is None:
            return None
        holes.append(coordinates)
    return Outline(ring, holes) if ring else None


async def _outlines(ids: list[dict[str, Any]], port: int, warnings: list[str]) -> dict[str, Outline]:
    """The plan outlines of the zones and slabs among the elements, by GUID."""
    try:
        result = await call_and_wait(
            "elements_get_details_of_elements",
            {"port": port, "params": {"elements": as_elements(ids), "fields": ["type", "details"]}},
        )
    except ToolError as error:
        warnings.append(f"Zone and slab outlines were not read, so their bounding boxes were used: {error}")
        return {}
    entries = result.get("detailsOfElements") if isinstance(result, dict) else None
    if not isinstance(entries, list) or len(entries) != len(ids):
        warnings.append("Zone and slab outlines were not read, so their bounding boxes were used: no answer.")
        return {}
    return {
        elem_id["guid"]: outline for elem_id, entry in zip(ids, entries, strict=True) if (outline := _outline(entry))
    }


def _new_set(guids: set[str] | list[str], label: str) -> str | None:
    return (
        ResultRegistry.register([{"elementId": {"guid": g}} for g in sorted(guids)], element_type=label)
        if guids
        else None
    )


async def _near(targets: list[dict], others: list[dict], distance: float, port: int) -> dict[str, Any]:
    target_boxes, target_missing = await _boxes(targets, port)
    other_boxes, other_missing = await _boxes(others, port)
    found = pairs_within(target_boxes, other_boxes, distance, keep=MAX_LISTED)
    response: dict[str, Any] = {
        "relation": "near",
        "distanceMeters": distance,
        "pairCount": found.pair_count,
        "matchedCount": len(found.matched),
        "matchedSetId": _new_set(found.matched, "Near"),
        "otherMatchedCount": len(found.other_matched),
        "otherMatchedSetId": _new_set(found.other_matched, "NearOther"),
        "nearest": [
            {"element": {"guid": a.key}, "other": {"guid": b.key}, "gapMeters": round(gap, 4)}
            for a, b, gap in found.nearest
        ],
        "note": "Measured between bounding boxes, which can be larger than diagonal or irregular elements.",
    }
    if target_missing or other_missing:
        response["withoutBoxCount"] = target_missing + other_missing
    return response


async def _inside(targets: list[dict], containers: list[dict], port: int) -> dict[str, Any]:
    target_boxes, target_missing = await _boxes(targets, port)
    container_boxes, container_missing = await _boxes(containers, port)
    warnings: list[str] = []
    outlines = await _outlines(containers, port, warnings)
    holders = containers_of(target_boxes, container_boxes, outlines)
    by_outline = sum(1 for box in container_boxes if box.key in outlines)
    per_container = Counter(holder for keys in holders.values() for holder in keys)
    response: dict[str, Any] = {
        "relation": "inside",
        "insideCount": len(holders),
        "insideSetId": _new_set(list(holders), "Inside"),
        "outsideCount": len(target_boxes) - len(holders),
        "byContainer": [
            {"container": {"guid": key}, "count": count} for key, count in per_container.most_common(MAX_LISTED)
        ],
        "containersByOutline": by_outline,
        "containersByBox": len(container_boxes) - by_outline,
        "note": "An element counts as inside when the center of its bounding box lies within the container: "
        "within the plan outline of a zone or slab with straight edges (not in its holes) and its height, "
        "otherwise within the container's bounding box, which is larger than a diagonal or curved container.",
    }
    if target_missing or container_missing:
        response["withoutBoxCount"] = target_missing + container_missing
    if warnings:
        response["warnings"] = warnings
    return response


async def _collides(targets: list[dict], others: list[dict], port: int) -> dict[str, Any]:
    result = await call_and_wait(
        "elements_get_collisions",
        {
            "port": port,
            "params": {
                "elementsGroup1": as_elements(targets),
                "elementsGroup2": as_elements(others),
            },
        },
    )
    collisions = result.get("collisions") if isinstance(result, dict) else None
    if not isinstance(collisions, list):
        raise ToolError(f"Archicad returned no collision result: {result}")
    matched = {c["elementId1"]["guid"] for c in collisions}
    other_matched = {c["elementId2"]["guid"] for c in collisions}
    return {
        "relation": "collides",
        "collisionCount": len(collisions),
        "bodyCollisionCount": sum(1 for c in collisions if c.get("hasBodyCollision")),
        "clearanceCollisionCount": sum(1 for c in collisions if c.get("hasClearenceCollision")),
        "matchedCount": len(matched),
        "matchedSetId": _new_set(matched, "Colliding"),
        "otherMatchedCount": len(other_matched),
        "otherMatchedSetId": _new_set(other_matched, "CollidingOther"),
        "examples": [
            {
                "element": c["elementId1"],
                "other": c["elementId2"],
                "body": c.get("hasBodyCollision"),
                "clearance": c.get("hasClearenceCollision"),
            }
            for c in collisions[:MAX_LISTED]
        ],
    }


async def _with_element_ids(response: dict[str, Any], port: int) -> dict[str, Any]:
    """Name the listed elements by their Element ID too, so the user can find them in Archicad."""
    pairs = response.get("nearest", []) + response.get("examples", [])
    refs = [ref for pair in pairs for ref in (pair["element"], pair["other"])]
    refs += [item["container"] for item in response.get("byContainer", [])]
    if not refs:
        return response
    warnings: list[str] = []
    labels = await labels_or_warning(sorted({ref["guid"] for ref in refs}), port, warnings)
    if warnings:
        response["warnings"] = [*response.get("warnings", []), *warnings]
    for ref in refs:
        if labels.get(ref["guid"]):
            ref["id"] = labels[ref["guid"]]
    return response


@mcp.tool(
    name="archicad_spatial_query",
    title="Find Elements Near, Inside or Colliding With Others",
    description=(
        "Relates two sets of elements in space. target_set and other_set: setIds from archicad_query, or lists of "
        "elements (the same set may be given twice). relation 'near': pairs whose bounding boxes are at most "
        "distance meters apart (0 = touching or overlapping), e.g. conduits within 0.5 m of walls. 'inside': "
        "target elements whose bounding-box center lies within an element of other_set, e.g. furniture in zones; "
        "zones and slabs with straight edges are tested by their plan outline and holes, others by their "
        "bounding box. "
        "'collides': Archicad's exact check of the real geometry, including MEP clearance zones. "
        "Returns counts, the nearest pairs or examples (with their Element IDs), and the matching elements as new "
        "setIds."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_spatial_query(
    relation: Literal["near", "inside", "collides"],
    target_set: str | list[Any],
    other_set: str | list[Any],
    distance: float = 0.0,
    port: int | None = None,
) -> dict[str, Any]:
    target_port = port or 19723
    if distance < 0:
        raise ToolError("distance must be zero or more meters.")
    targets = _resolve(target_set, "target_set")
    others = _resolve(other_set, "other_set")
    if relation == "near":
        response = await _near(targets, others, distance, target_port)
    elif relation == "inside":
        response = await _inside(targets, others, target_port)
    elif relation == "collides":
        response = await _collides(targets, others, target_port)
    else:
        raise ToolError("relation must be 'near', 'inside' or 'collides'.")
    return await _with_element_ids(response, target_port)
