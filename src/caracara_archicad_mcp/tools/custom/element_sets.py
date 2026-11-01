"""Turning what a tool receives as 'target_set' into element identifiers.

Tools accept either a setId from archicad_query or a list of elements, and elements arrive
in several shapes ({'elementId': {'guid': ...}}, {'guid': ...}, a bare GUID string).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad.models.tapir.types import ElementType

from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom.functions import call_and_collect

# Archicad's element type names by their lowercase spelling without underscores.
_ELEMENT_TYPES = {element_type.value.lower(): element_type.value for element_type in ElementType}


def normalize_element_type(target: str) -> str:
    """'walls', 'Curtain_Walls', 'polyline' -> Archicad's own spelling ('Wall', 'CurtainWall', 'PolyLine')."""
    key = target.strip().lower().replace("_", "")
    for candidate in (key, key.removesuffix("es"), key.removesuffix("s")):
        if candidate in _ELEMENT_TYPES:
            return _ELEMENT_TYPES[candidate]
    return target.strip()


def element_id(elem: Any) -> dict[str, Any] | None:
    """Return the {'guid': ...} identifier of a stored element, or None if it has none."""
    elem_id = elem.get("elementId", elem) if isinstance(elem, dict) else elem
    if isinstance(elem_id, dict):
        guid = elem_id.get("guid")
    else:
        guid = getattr(elem_id, "guid", elem_id if isinstance(elem_id, str) else None)
    if not isinstance(guid, str) or not guid.strip():
        return None
    return {"guid": guid}


def element_ids(elements: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split elements into usable identifiers and itemized failures for malformed entries."""
    ids: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for index, elem in enumerate(elements):
        elem_id = element_id(elem)
        if elem_id is None:
            invalid.append({"index": index, "error": {"message": "Entry has no element GUID; skipped."}})
        else:
            ids.append(elem_id)
    return ids, invalid


def as_elements(element_ids: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{'guid': ...}] -> [{'elementId': {'guid': ...}}], the shape Archicad commands take."""
    return [{"elementId": e} for e in element_ids]


async def element_labels(guids: list[str], port: int) -> dict[str, str | None]:
    """The Element ID shown in Archicad (e.g. 'SW - 005') of each GUID, so results can name elements
    the way the user knows them; None for an element that no longer exists."""
    if not guids:
        return {}
    details = await call_and_collect(
        "elements_get_details_of_elements",
        {"port": port, "params": {"elements": as_elements([{"guid": g} for g in guids]), "fields": ["id"]}},
        "detailsOfElements",
    )
    if len(details) != len(guids):
        raise ToolError("Archicad returned details for a different number of elements than requested.")
    return {
        guid: detail.get("id") if isinstance(detail, dict) and "error" not in detail else None
        for guid, detail in zip(guids, details, strict=True)
    }


async def labels_or_warning(guids: list[str], port: int, warnings: list[str]) -> dict[str, str | None]:
    """element_labels for naming listed elements; when the IDs cannot be read, none, and a warning saying so."""
    try:
        return await element_labels(guids, port)
    except ToolError as error:
        warnings.append(f"Element IDs were not read: {error}")
        return {}


def target_elements(target_set: str | list[Any], label: str = "target_set") -> list[Any]:
    """The elements a setId stands for, or the given list itself."""
    if isinstance(target_set, str):
        return ResultRegistry.get_or_tool_error(target_set)
    if isinstance(target_set, list):
        return target_set
    raise ToolError(f"Invalid {label}: must be a setId (str) or a list of elements.")
