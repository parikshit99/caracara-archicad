"""archicad_find_properties: the property definitions whose name matches what the user calls them.

Listing every property costs the conversation about 1,600 built-in definitions before the
project's own; this reads them in the server (GetAllProperties) and returns only the closest
matches, with the propertyId that property commands take. It reads definitions only, never
property values.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.property_search import MIN_SCORE, normalize, score
from caracara_archicad_mcp.tools.custom.functions import call_and_collect

MAX_RESULTS = 50


def _match(definition: dict[str, Any], match: float) -> dict[str, Any]:
    entry = {
        "propertyId": definition.get("propertyId"),
        "name": definition.get("propertyName"),
        "group": definition.get("propertyGroupName"),
        "type": definition.get("propertyType"),
        "valueType": definition.get("propertyValueType"),
        "collectionType": definition.get("propertyCollectionType"),
        "measureType": definition.get("propertyMeasureType"),
        "editable": definition.get("propertyIsEditable"),
        "score": round(match, 2),
    }
    return {key: value for key, value in entry.items() if value is not None}


@mcp.tool(
    name="archicad_find_properties",
    title="Find Archicad Properties by Name",
    description=(
        "Finds property definitions (built-in and the project's own) by name, so the propertyId for reading or "
        "setting a property is found without listing every property. query: the name as the user says it, e.g. "
        "'fire rating'; small typos and other word orders are found too, and the group name counts. "
        f"Returns up to limit (at most {MAX_RESULTS}) matches, best first, each with propertyId, name, group, type "
        "(StaticBuiltIn, DynamicBuiltIn or Custom), valueType, collectionType, measureType, editable and a score "
        "from 0 to 1. Reads definitions only, never property values."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_find_properties(query: str, limit: int = 10, port: int | None = None) -> dict[str, Any]:
    if not normalize(query):
        raise ToolError("query must contain a word of the property's name, e.g. 'fire rating'.")
    if not 1 <= limit <= MAX_RESULTS:
        raise ToolError(f"limit must be between 1 and {MAX_RESULTS}.")
    definitions = [
        item
        for item in await call_and_collect("properties_get_all_properties", {"port": port or 19723}, "properties")
        if isinstance(item, dict)
    ]
    scored = [
        (score(query, str(item.get("propertyName", "")), str(item.get("propertyGroupName", ""))), item)
        for item in definitions
    ]
    matches = sorted(
        (pair for pair in scored if pair[0] >= MIN_SCORE),
        key=lambda pair: (-pair[0], str(pair[1].get("propertyGroupName")), str(pair[1].get("propertyName"))),
    )
    return {
        "query": query,
        "matchCount": len(matches),
        "searchedCount": len(definitions),
        "properties": [_match(item, match) for match, item in matches[:limit]],
    }
