"""Hand-written behaviour layered over generated commands.

The generated modules are rewritten by scripts/generate_tools.py and must stay
pure generator output. Commands that return virtual-set receipts, accept
targetSetId handles, or project fields are re-registered here after the
generated modules have been imported (see tools/__init__.py).
"""

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad import ConnHeader
from multiconn_archicad.models.official.commands import GetElementsByClassificationParameters
from multiconn_archicad.models.tapir.commands import FilterElementsParameters, GetElementsByTypeParameters

from caracara_archicad_mcp.field_masking import prune_payload
from caracara_archicad_mcp.result_registry import ResultRegistry, SetProject, project_identity
from caracara_archicad_mcp.tools.custom.models import (
    DeleteElementsInput,
    GetDetailsOfElementsInput,
    GetDetailsOfPropertiesInput,
    HighlightElementsInput,
    SetPropertyValuesOfElementsInput,
)
from caracara_archicad_mcp.tools.tool_registry import override_tool_for_dispatch

# Fields GetDetailsOfElements can project server-side; anything else is pruned client-side.
SERVER_DETAIL_FIELDS = {
    "type",
    "id",
    "floorIndex",
    "layerIndex",
    "drawIndex",
    "details",
    "floorPlanPolygons",
    "hotlinkId",
}
DEFAULT_HIGHLIGHT_COLOR = [0, 120, 255, 255]


def _dump(value: Any, **kwargs: Any) -> Any:
    return value.model_dump(by_alias=True, **kwargs) if hasattr(value, "model_dump") else value


def _open_project(conn_header: ConnHeader) -> SetProject:
    """Asked each time: the connection's cached project is not updated when another project is opened."""
    info = conn_header.core.post_tapir_command(command="GetProjectInfo")
    return SetProject(int(conn_header.port), project_identity(info))


def _set_receipt(raw_result: Any, element_type: str, project: SetProject) -> Any:
    """Store a listing's elements as a virtual set and return a compact receipt instead."""
    if isinstance(raw_result, dict) and "elements" in raw_result:
        elements = raw_result["elements"]
        set_id = ResultRegistry.register(elements, element_type=element_type, project=project)
        return {"setId": set_id, "count": len(elements), "elementType": element_type}
    return raw_result


def _set_elements(conn_header: ConnHeader, set_id: str) -> list[Any]:
    project = _open_project(conn_header) if ResultRegistry.project_of(set_id) else None
    return ResultRegistry.get_or_tool_error(set_id, project)


def _target_elements(
    conn_header: ConnHeader, target_set_id: str | None, elements: list[Any] | None
) -> list[Any] | None:
    if target_set_id:
        return _set_elements(conn_header, target_set_id)
    if elements is not None:
        return [_dump(e) for e in elements]
    return None


# --- Virtual set receipts -------------------------------------------------------


def get_elements_by_type(conn_header: ConnHeader, params: GetElementsByTypeParameters) -> dict:
    """Returns every element of the given type as a virtual set receipt (setId, count, elementType)."""
    project = _open_project(conn_header)
    raw_result = conn_header.core.post_tapir_command(
        command="GetElementsByType", parameters=params.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    element_type = params.elementType.value if hasattr(params.elementType, "value") else str(params.elementType)
    return _set_receipt(raw_result, element_type, project)


def filter_elements(conn_header: ConnHeader, params: FilterElementsParameters) -> dict:
    """Tests elements by the given criterias and returns the matches as a virtual set receipt."""
    project = _open_project(conn_header)
    raw_result = conn_header.core.post_tapir_command(
        command="FilterElements", parameters=params.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    return _set_receipt(raw_result, "Filtered", project)


def get_elements_by_classification(conn_header: ConnHeader, params: GetElementsByClassificationParameters) -> dict:
    """Returns every element with the given classification as a virtual set receipt."""
    project = _open_project(conn_header)
    raw_result = conn_header.core.post_command(
        command="API.GetElementsByClassification",
        parameters=params.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    return _set_receipt(raw_result, "Classified", project)


# --- targetSetId mutations ------------------------------------------------------


def delete_elements(conn_header: ConnHeader, params: DeleteElementsInput) -> dict:
    """Deletes elements. Accepts targetSetId or explicit elements array."""
    elements = _target_elements(conn_header, params.target_set_id, params.elements)
    if elements is None:
        raise ToolError("Either 'targetSetId' or 'elements' must be provided to delete_elements.")
    return conn_header.core.post_tapir_command(command="DeleteElements", parameters={"elements": elements})


def highlight_elements(conn_header: ConnHeader, params: HighlightElementsInput) -> dict:
    """Highlights the elements of targetSetId or the elements array; an empty array clears highlights."""
    elements = _target_elements(conn_header, params.target_set_id, params.elements) or []
    colors = [_dump(c) for c in params.highlighted_colors or []]
    # Tapir requires highlightedColors, with exactly one color per element.
    if not elements:
        colors = []
    elif len(colors) <= 1:
        colors = (colors or [DEFAULT_HIGHLIGHT_COLOR]) * len(elements)
    elif len(colors) != len(elements):
        raise ToolError(
            f"highlightedColors has {len(colors)} colors for {len(elements)} elements; "
            "give one color for all elements, or one per element."
        )
    payload: dict[str, Any] = {"elements": elements, "highlightedColors": colors}
    if params.wireframe_3d is not None:
        payload["wireframe3D"] = params.wireframe_3d
    if params.non_highlighted_color is not None:
        payload["nonHighlightedColor"] = _dump(params.non_highlighted_color)
    return conn_header.core.post_tapir_command(command="HighlightElements", parameters=payload)


def set_property_values_of_elements(conn_header: ConnHeader, params: SetPropertyValuesOfElementsInput) -> dict:
    """Sets the property values of elements, accepting targetSetId or explicit elementPropertyValues."""
    if not params.target_set_id:
        if params.element_property_values is None:
            raise ToolError("Either 'targetSetId' or 'elementPropertyValues' must be provided.")
        return conn_header.core.post_tapir_command(
            command="SetPropertyValuesOfElements",
            parameters={
                "elementPropertyValues": [
                    _dump(v, mode="json", exclude_none=True) for v in params.element_property_values
                ]
            },
        )

    elements = _set_elements(conn_header, params.target_set_id)
    items_to_apply: list[dict[str, Any]] = []
    if params.property_id is not None and params.property_value is not None:
        items_to_apply.append({"propertyId": _dump(params.property_id), "propertyValue": _dump(params.property_value)})
    elif params.property_values or params.element_property_values:
        for item in params.property_values or params.element_property_values:
            d = _dump(item)
            if not isinstance(d, dict):
                raise ToolError("Each property value must be an object with propertyId and propertyValue.")
            # Look keys up by presence, not truthiness: 0, False and "" are valid property values.
            items_to_apply.append(
                {
                    "propertyId": d["propertyId"] if "propertyId" in d else d.get("property_id"),
                    "propertyValue": d["propertyValue"] if "propertyValue" in d else d.get("property_value"),
                }
            )
    else:
        raise ToolError("When 'targetSetId' is provided, propertyId/propertyValue or propertyValues must be specified.")

    expanded: list[dict[str, Any]] = []
    for elem in elements:
        elem_id = elem.get("elementId", elem) if isinstance(elem, dict) else elem
        if not isinstance(elem_id, dict) or "guid" not in elem_id:
            elem_id = {"guid": str(getattr(elem_id, "guid", elem_id))}
        for p_item in items_to_apply:
            expanded.append({"elementId": elem_id, **p_item})

    return conn_header.core.post_tapir_command(
        command="SetPropertyValuesOfElements", parameters={"elementPropertyValues": expanded}
    )


# --- Field projection -----------------------------------------------------------


def get_details_of_elements(conn_header: ConnHeader, params: GetDetailsOfElementsInput) -> dict:
    """Gets element details; 'fields' returns only those fields and prunes the rest (geometry, memos)."""
    tapir_params: dict[str, Any] = {"elements": [_dump(e, mode="json", exclude_none=True) for e in params.elements]}
    if params.fields and all(f in SERVER_DETAIL_FIELDS for f in params.fields):
        tapir_params["fields"] = list(params.fields)

    raw_result = conn_header.core.post_tapir_command(command="GetDetailsOfElements", parameters=tapir_params)
    return prune_payload(raw_result, params.fields) if params.fields else raw_result


def get_details_of_properties(conn_header: ConnHeader, params: GetDetailsOfPropertiesInput) -> dict:
    """Returns the details of property definitions with optional field projection."""
    raw_result = conn_header.core.post_command(
        command="API.GetDetailsOfProperties",
        parameters={"propertyIds": [_dump(p, mode="json", exclude_none=True) for p in params.property_ids]},
    )
    return prune_payload(raw_result, params.fields) if params.fields else raw_result


override_tool_for_dispatch(
    "elements_get_elements_by_type",
    get_elements_by_type,
    description=(
        "Returns every element of the given type as a virtual set receipt (setId, count, elementType). "
        "Use the optional filter parameter for filtering."
    ),
)
override_tool_for_dispatch(
    "elements_filter_elements",
    filter_elements,
    description="Tests elements by the given criterias and returns the matches as a virtual set receipt.",
)
override_tool_for_dispatch(
    "elements_get_elements_by_classification",
    get_elements_by_classification,
    description="Returns every element with the given classification as a virtual set receipt.",
)
override_tool_for_dispatch(
    "elements_delete_elements",
    delete_elements,
    DeleteElementsInput,
    description=(
        "Deletes elements. Accepts targetSetId or explicit elements array. Returns an execution result for each "
        "input element."
    ),
)
override_tool_for_dispatch(
    "elements_highlight_elements",
    highlight_elements,
    HighlightElementsInput,
    description=(
        "Highlights the elements given in targetSetId or elements array. In case of empty elements array removes "
        "all previously set highlights."
    ),
)
override_tool_for_dispatch(
    "properties_set_property_values_of_elements",
    set_property_values_of_elements,
    SetPropertyValuesOfElementsInput,
    description="Sets the property values of elements. Accepts targetSetId or explicit elementPropertyValues.",
)
override_tool_for_dispatch(
    "elements_get_details_of_elements",
    get_details_of_elements,
    GetDetailsOfElementsInput,
    description=(
        "Gets the details of the given elements (geometry parameters etc). Use the optional fields parameter to "
        "return only the fields you need and prune unrequested properties (e.g. polygonVertices, internalMemos)."
    ),
)
override_tool_for_dispatch(
    "properties_get_details_of_properties",
    get_details_of_properties,
    GetDetailsOfPropertiesInput,
    description="Returns the details of property definitions with optional field projection.",
)
