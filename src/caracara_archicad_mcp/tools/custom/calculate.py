"""archicad_calculate: exact quantity calculations over a set of elements, written as read-only SQL.

The tool reads the elements' type, ID, story, layer and Archicad's own quantities (lengths,
areas, volumes, per building material), loads them into a throwaway in-memory database
(see caracara_archicad_mcp.calculation) and runs one SELECT. Only the query's result goes
back to the model, so a total over thousands of elements costs a few rows of context.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.calculation import (
    CalculationError,
    Table,
    UnknownNameError,
    column_names,
    describe,
    run_query,
    snake_case,
    sqlite_type,
)
from caracara_archicad_mcp.tools.custom.element_sets import element_ids, target_elements
from caracara_archicad_mcp.tools.custom.functions import call_and_collect, call_and_wait

MAX_ELEMENTS = 100_000
DETAIL_FIELDS = ["type", "id", "floorIndex", "layerIndex"]
UNITS = "Lengths are in meters, areas in square meters, volumes in cubic meters."

ELEMENT_COLUMNS = {
    "guid": "TEXT",
    "element_id": "TEXT",
    "type": "TEXT",
    "story_index": "INTEGER",
    "story": "TEXT",
    "layer": "TEXT",
}
COMPONENT_COLUMNS = {
    "guid": "TEXT",
    "type": "TEXT",
    "story_index": "INTEGER",
    "story": "TEXT",
    "layer": "TEXT",
    "material": "TEXT",
    "volume": "REAL",
    "projected_area": "REAL",
    "is_core": "INTEGER",
    "is_finish": "INTEGER",
}
# Archicad lists the building materials of a column or beam twice: on the element itself and on each
# of its segments. The segments are kept, since they can differ in material.
SEGMENT_OWNERS = {"ColumnSegment": "Column", "BeamSegment": "Beam"}


async def _names(port: int, warnings: list[str]) -> tuple[dict[int, str], dict[int, str], dict[str, str]]:
    """Story names by index, layer names by index and building material names by GUID, as far as available."""
    stories: dict[int, str] = {}
    layers: dict[int, str] = {}
    materials: dict[str, str] = {}
    try:
        result = await call_and_wait("project_get_stories", {"port": port})
        if not isinstance(result, dict) or not isinstance(result.get("stories"), list):
            raise ToolError(f"Archicad returned no stories: {result}")
        stories = {int(story["index"]): story["name"] for story in result["stories"]}
    except (ToolError, KeyError, TypeError, ValueError) as error:
        warnings.append(f"Story names are missing: {error}")
    for attribute_type, names, key in (("Layer", layers, "index"), ("BuildingMaterial", materials, "guid")):
        try:
            for header in await call_and_collect(
                "attributes_get_attributes_by_type",
                {"port": port, "params": {"attributeType": attribute_type}},
                "attributes",
            ):
                names[int(header["index"]) if key == "index" else header["attributeId"]["guid"]] = header["name"]
        except (ToolError, KeyError, TypeError, ValueError) as error:
            warnings.append(f"{attribute_type} names are missing: {error}")
    return stories, layers, materials


def _typed_columns(fixed: dict[str, str], rows: list[dict[str, Any]]) -> dict[str, str]:
    columns = dict(fixed)
    for row in rows:
        for name, value in row.items():
            if name not in columns and (kind := sqlite_type(value)) is not None:
                columns[name] = kind
    return columns


async def _load_tables(ids: list[dict[str, Any]], port: int, warnings: list[str]) -> tuple[list[Table], str | None]:
    """The 'elements' and 'components' tables, and a note on column and beam materials when there is one."""
    elements = [{"elementId": elem_id} for elem_id in ids]
    details = await call_and_collect(
        "elements_get_details_of_elements",
        {"port": port, "params": {"elements": elements, "fields": DETAIL_FIELDS}},
        "detailsOfElements",
    )
    quantities = await call_and_collect(
        "elements_get_quantities_of_elements", {"port": port, "params": {"elements": elements}}, "quantitiesOfElements"
    )
    if len(details) != len(ids) or len(quantities) != len(ids):
        raise ToolError("Archicad returned data for a different number of elements than requested.")
    stories, layers, materials = await _names(port, warnings)

    element_rows: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    for elem_id, detail, quantity in zip(ids, details, quantities, strict=True):
        detail = detail if isinstance(detail, dict) and "error" not in detail else {}
        quantity = quantity if isinstance(quantity, dict) and "error" not in quantity else {}
        story_index = int(detail["floorIndex"]) if isinstance(detail.get("floorIndex"), int | float) else None
        layer_index = int(detail["layerIndex"]) if isinstance(detail.get("layerIndex"), int | float) else None
        common = {
            "guid": elem_id["guid"],
            "type": detail.get("type") or quantity.get("type"),
            "story_index": story_index,
            "story": stories.get(story_index) if story_index is not None else None,
            "layer": layers.get(layer_index) if layer_index is not None else None,
        }
        row = {**common, "element_id": detail.get("id")}
        for name, value in (quantity.get("quantities") or {}).items():
            if isinstance(value, bool):
                row[snake_case(name)] = value
            elif isinstance(value, int | float):
                # Measurements are real numbers even when Archicad sends a whole one (60 for a 60 m² slab).
                row[snake_case(name)] = float(value)
        element_rows.append(row)
        for component in quantity.get("composites") or []:
            material_guid = (component.get("buildingMaterialId") or {}).get("attributeId", {}).get("guid")
            component_rows.append(
                {
                    **common,
                    "material": materials.get(material_guid),
                    "volume": component.get("volume"),
                    "projected_area": component.get("projectedArea"),
                    "is_core": component.get("isCore"),
                    "is_finish": component.get("isFinish"),
                }
            )
    component_rows, note = _without_doubled_owners(component_rows)
    tables = [
        Table("elements", _typed_columns(ELEMENT_COLUMNS, element_rows), element_rows),
        Table("components", COMPONENT_COLUMNS, component_rows),
    ]
    return tables, note


def _without_doubled_owners(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    """Leave out the material rows of columns and beams whose segments are in the set too, and say so."""
    types = {row["type"] for row in rows}
    doubled = {owner for segment, owner in SEGMENT_OWNERS.items() if segment in types and owner in types}
    if not doubled:
        return rows, None
    note = (
        f"'components' has no rows for {', '.join(sorted(doubled))} elements: their building materials are "
        "counted on their segments, which are in the set too."
    )
    return [row for row in rows if row["type"] not in doubled], note


@mcp.tool(
    name="archicad_calculate",
    title="Calculate Quantities of Archicad Elements",
    description=(
        "Calculates exact totals over a set of elements with one read-only SQL SELECT, instead of adding "
        "numbers up yourself. target_set: a setId from archicad_query, or a list of elements. "
        "Tables: 'elements' (one row per element: guid, element_id, type, story_index, story, layer, and "
        "Archicad's quantities such as volume, gross_volume, length, area, height; columns depend on the "
        "element types) and 'components' (one row per building material skin: guid, type, story_index, story, "
        "layer, material, volume, projected_area, is_core, is_finish). "
        "Slabs have their area in top_surface and bottom_surface, not in area. Zones hold the room's air "
        "volume and floor area, so leave type 'Zone' out when adding up building volumes. A column's or beam's "
        "volume is on its ColumnSegment or BeamSegment rows. "
        f"{UNITS} Call without sql to list the columns that exist for this set. "
        "Example: SELECT story, type, ROUND(SUM(volume), 2) AS m3 FROM elements GROUP BY story, type. "
        "The data stays on this computer in a temporary in-memory database; only the query result is returned."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_calculate(
    target_set: str | list[Any],
    sql: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    target_port = port or 19723
    ids, invalid = element_ids(await target_elements(target_set, target_port))
    if not ids:
        raise ToolError("The set contains no elements to calculate over.")
    if len(ids) > MAX_ELEMENTS:
        raise ToolError(f"The set has {len(ids)} elements; calculate over at most {MAX_ELEMENTS} at a time.")

    warnings: list[str] = []
    if invalid:
        warnings.append(f"{len(invalid)} entries have no element GUID and were left out.")
    tables, note = await _load_tables(ids, target_port, warnings)
    # The note about column and beam materials only matters to queries that read them.
    if note and (sql is None or "components" in sql.lower()):
        warnings.append(note)

    if sql is None:
        response: dict[str, Any] = {"tables": describe(tables), "units": UNITS}
    else:
        try:
            response = await asyncio.to_thread(run_query, tables, sql)
        except UnknownNameError as error:
            raise ToolError(f"{error} Available tables: {column_names(tables)}") from None
        except CalculationError as error:
            raise ToolError(str(error)) from None
    if warnings:
        response["warnings"] = warnings
    return response
