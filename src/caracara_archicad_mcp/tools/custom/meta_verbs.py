"""archicad_query and archicad_mutate: the common element work, one tool call each.

archicad_query finds elements (by type, selection, classification or filter), stores them
as a set and returns its setId with the fields asked for. archicad_mutate changes the
elements of a set with one Archicad command, which is one Undo step.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server.mcpserver import (
    AcceptedElicitation,
    CancelledElicitation,
    Context,
    DeclinedElicitation,
    Elicit,
    Resolve,
)
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.safety import ensure_allowed
from caracara_archicad_mcp.tools.custom.element_sets import (
    element_ids,
    normalize_element_type,
    open_project,
    target_elements,
)
from caracara_archicad_mcp.tools.custom.functions import call_tool_payload, is_running
from caracara_archicad_mcp.tools.custom.mutation_checks import (
    confirmation_reasons,
    describe_hosted,
    hosted_elements,
    label_listed,
    preflight,
    skipped_elements,
)

log = logging.getLogger(__name__)

# Element identifiers returned inline by output='ids'; larger sets stay behind their setId.
MAX_INLINE_ELEMENTS = 1000
# Failed elements itemized in a mutation response; the rest are only counted.
MAX_ITEMIZED_FAILURES = 50


def _itemize(result: Any, ids: list[dict[str, Any]], invalid: list[dict[str, Any]]) -> Any:
    """Attach per-element failures to a batch result without raising.

    Elements deleted, moved or locked after their set was created come back from
    Tapir as failed entries of 'executionResults' (positionally matching the
    request); they are listed in 'failedElements' so the batch result stays usable.
    """
    if is_running(result) or not isinstance(result, dict):
        return result
    failures = list(invalid)
    execution_results = result.get("executionResults")
    if isinstance(execution_results, list):
        aligned = len(execution_results) == len(ids)
        for index, item in enumerate(execution_results):
            if isinstance(item, dict) and item.get("success") is False:
                failure: dict[str, Any] = {"error": item.get("error", {})}
                if aligned:
                    failure["elementId"] = ids[index]
                else:
                    failure["index"] = index
                failures.append(failure)
    if not failures:
        return result
    itemized = dict(result)
    itemized["failedCount"] = len(failures)
    itemized["failedElements"] = failures[:MAX_ITEMIZED_FAILURES]
    return itemized


async def _all_pages(name: str, arguments: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    """Call a paginated listing and follow next_page_token until every element is collected."""
    result = await call_tool_payload(name, arguments)
    if is_running(result):
        return result, []
    elements = list(result.get("elements", []))
    token = result.get("next_page_token")
    while token:
        page = await call_tool_payload(name, {**arguments, "page_token": token})
        elements.extend(page.get("elements", []))
        token = page.get("next_page_token")
    return result, elements


def _vector(payload: dict[str, Any]) -> dict[str, float]:
    vec = payload.get("vector", payload)
    if not isinstance(vec, dict):
        raise ToolError("'vector' must be an object with numeric 'x', 'y' and 'z'.")
    try:
        return {axis: float(vec.get(axis, 0.0)) for axis in ("x", "y", "z")}
    except (TypeError, ValueError):
        raise ToolError("'vector' components 'x', 'y' and 'z' must be numbers.") from None


@dataclass
class _Found:
    """What a query found: the listing's raw result, its elements, and the set they are stored in, if any."""

    raw: Any
    elements: list[dict[str, Any]]
    element_type: str
    set_id: str | None = None


def _from_receipt(raw: Any, element_type: str) -> _Found:
    """A listing whose override already stored its elements as a set and returned a receipt."""
    set_id = raw.get("setId") if isinstance(raw, dict) else None
    if set_id and ResultRegistry.has(set_id):
        elements = ResultRegistry.get(set_id)
    else:
        elements = raw.get("elements", []) if isinstance(raw, dict) else []
    return _Found(raw, elements, element_type, set_id)


async def _find(target: str, filter: dict[str, Any] | None, port: int) -> _Found:
    kind = target.strip().lower()
    filters = {"filters": filter["filters"]} if filter and "filters" in filter else {}

    if kind in ("selected", "selection"):
        raw, elements = await _all_pages("elements_get_selected_elements", {"port": port})
        return _Found(raw, elements, "Selected")
    if kind in ("all", "all_elements", "elements"):
        raw, elements = await _all_pages("elements_get_all_elements", {"port": port, "params": filters})
        return _Found(raw, elements, "Element")
    if kind in ("classified", "classification") or (filter and "classificationItemId" in filter):
        if not filter or "classificationItemId" not in filter:
            raise ToolError("When target is 'classified', filter must include 'classificationItemId'.")
        params = {"classificationItemId": filter["classificationItemId"]}
        raw = await call_tool_payload("elements_get_elements_by_classification", {"port": port, "params": params})
        return _from_receipt(raw, "Classified")
    if kind in ("filter", "filtered"):
        if not filter:
            raise ToolError("When target is 'filter', filter dictionary must be provided.")
        raw = await call_tool_payload("elements_filter_elements", {"port": port, "params": filter})
        return _from_receipt(raw, "Filtered")

    element_type = normalize_element_type(target)
    params = {"elementType": element_type, **filters}
    raw = await call_tool_payload("elements_get_elements_by_type", {"port": port, "params": params})
    return _from_receipt(raw, element_type)


async def _query_output(found: _Found, set_id: str, output: list[str] | str | None, port: int) -> dict[str, Any]:
    response: dict[str, Any] = {"setId": set_id, "count": len(found.elements), "elementType": found.element_type}
    if output in (None, "set", "count"):
        return response

    if output in ("ids", "elements", "guid", "guids"):
        response["elements"] = found.elements[:MAX_INLINE_ELEMENTS]
        if len(found.elements) > MAX_INLINE_ELEMENTS:
            response["truncated"] = True
        return response

    if isinstance(output, list) or output in ("details", "full"):
        details_params: dict[str, Any] = {"elements": found.elements}
        if isinstance(output, list) and output:
            details_params["fields"] = output
        details = await call_tool_payload("elements_get_details_of_elements", {"port": port, "params": details_params})
        if is_running(details):
            return {**response, **details}
        response["details"] = details.get("detailsOfElements", [])
        for key in ("next_page_token", "unknownFields"):
            if details.get(key):
                response[key] = details[key]
        return response

    raise ToolError(
        f"Unsupported output specification '{output}'. Supported: 'set', 'count', 'ids', or list of field names."
    )


@mcp.tool(
    name="archicad_query",
    title="Query Archicad Elements & Metadata",
    description=(
        "Finds elements and reads their metadata. "
        "target: an element type ('Wall', 'Slab', 'Door', ...) or 'all', 'selected', 'classified', 'filter'. "
        "filter: optional criteria, e.g. {'filters': ['IsVisibleIn3D']} or {'classificationItemId': ...}. "
        "output: 'set' (default) returns an opaque setId and count; 'ids' returns element identifiers "
        f"(up to {MAX_INLINE_ELEMENTS}); a list of field names (e.g. ['id', 'storyIndex']) returns only those fields."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_query(
    target: str,
    filter: dict[str, Any] | None = None,
    output: list[str] | str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    """Execute a polymorphic query in Archicad."""
    target_port = port or 19723
    found = await _find(target, filter, target_port)
    if is_running(found.raw):
        # The command's own wrapper registers the set when the job completes.
        return found.raw
    set_id = found.set_id
    if not set_id or not ResultRegistry.has(set_id):
        project = await open_project(target_port)
        set_id = ResultRegistry.register(found.elements, element_type=found.element_type, project=project)
    return await _query_output(found, set_id, output, target_port)


@dataclass
class _Mutation:
    """What every action of archicad_mutate works on."""

    element_ids: list[dict[str, Any]]
    invalid: list[dict[str, Any]]
    set_id: str
    payload: dict[str, Any] | None
    port: int
    confirm: bool = False

    def require_payload(self, action: str, example: str = "") -> dict[str, Any]:
        if not self.payload:
            raise ToolError(f"Payload must be provided for '{action}' action{example}.")
        return self.payload

    def elements(self) -> list[dict[str, Any]]:
        return [{"elementId": e} for e in self.element_ids]

    async def run(self, command: str, params: dict[str, Any]) -> Any:
        """Run one Tapir command, which Archicad applies as one Undo step, and itemize failed elements."""
        result = await call_tool_payload(command, {"port": self.port, "params": params})
        return _itemize(result, self.element_ids, self.invalid)


async def _delete(m: _Mutation) -> Any:
    if not m.confirm and m.element_ids:
        hosted, _ = await hosted_elements(m.element_ids, m.port)
        if hosted:
            warnings: list[str] = []
            held: dict[str, Any] = {
                "deleted": False,
                "needsConfirmation": True,
                "message": (
                    f"Nothing was deleted. These elements host {describe_hosted(hosted)}, which Archicad would "
                    "delete too. Ask the user; if they agree, repeat this call with confirm=true."
                ),
                "hostedElementCount": len(hosted),
                "hostedElements": await label_listed(hosted[:MAX_ITEMIZED_FAILURES], m.port, warnings),
            }
            if warnings:
                held["warnings"] = warnings
            return held
    return await m.run("elements_delete_elements", {"elements": m.elements()})


async def _highlight(m: _Mutation) -> Any:
    params: dict[str, Any] = {"elements": m.elements()}
    for key in ("highlightedColors", "wireframe3D", "nonHighlightedColor"):
        if m.payload and key in m.payload:
            params[key] = m.payload[key]
    return await m.run("elements_highlight_elements", params)


async def _set_property(m: _Mutation) -> Any:
    params = dict(m.require_payload("set_property"))
    params["targetSetId"] = m.set_id
    return await m.run("properties_set_property_values_of_elements", params)


async def _move(m: _Mutation) -> Any:
    payload = m.require_payload("move", " (e.g. {'x': 1.0, 'y': 0.0, 'z': 0.0})")
    vector, copy = _vector(payload), payload.get("copy", False)
    moves = [{"elementId": e, "moveVector": vector, "copy": copy} for e in m.element_ids]
    return await m.run("elements_move_elements", {"elementsWithMoveVectors": moves})


async def _rotate(m: _Mutation) -> Any:
    payload = m.require_payload("rotate")
    rotation, copy = payload.get("rotation", payload), payload.get("copy", False)
    rotations = [{"elementId": e, "rotation": rotation, "copy": copy} for e in m.element_ids]
    return await m.run("elements_rotate_elements", {"elementsWithRotations": rotations})


async def _set_details(m: _Mutation) -> Any:
    payload = m.require_payload("set_details")
    details = payload.get("details", payload)
    if not isinstance(details, dict):
        raise ToolError("'details' must be an object.")
    items = [{"elementId": e, "details": details} for e in m.element_ids]
    return await m.run("elements_set_details_of_elements", {"elementsWithDetails": items})


# Every accepted spelling of an action, and the function that carries it out.
MUTATION_ACTIONS: dict[str, Callable[[_Mutation], Awaitable[Any]]] = {
    **dict.fromkeys(("delete", "destroy", "remove"), _delete),
    **dict.fromkeys(("highlight", "select"), _highlight),
    **dict.fromkeys(
        ("set_property", "set_properties", "update_property", "update_properties", "property_update"),
        _set_property,
    ),
    **dict.fromkeys(("move", "translate"), _move),
    "rotate": _rotate,
    **dict.fromkeys(("set_details", "update_details"), _set_details),
}


def _held_for_skipped(report: dict[str, Any], warnings: list[str], total: int) -> dict[str, Any] | None:
    """The reply that stops a change Archicad would apply to only part of the set, so the user decides first."""
    skipped = report.get("missingCount", 0) + report.get("notEditableCount", 0)
    if not skipped:
        return None
    return {
        "changed": False,
        "needsConfirmation": True,
        "message": (
            f"Nothing was changed: Archicad would skip {skipped} of {total} elements (see 'missing' "
            "and 'notEditable'). Tell the user. Query again to work on the model as it is now, or, if they agree, "
            "repeat this call with confirm=true to change the other elements."
        ),
        **report,
        "warnings": warnings,
    }


def _set_age(target_set: str | list[Any]) -> dict[str, Any]:
    """{'setAgeSeconds': ...} for a setId: how long ago the set was found, since it does not follow later changes."""
    age = ResultRegistry.age_seconds(target_set) if isinstance(target_set, str) else None
    return {"setAgeSeconds": round(age)} if age is not None else {}


async def _with_check_results(result: Any, port: int, checked: dict[str, Any]) -> Any:
    """Add what the check before the change found (set age, missing elements, warnings) and the Element IDs
    of failed elements."""
    if is_running(result) or not isinstance(result, dict):
        return result
    warnings = [*result.get("warnings", []), *checked.get("warnings", [])]
    extra = {key: value for key, value in checked.items() if key != "warnings"}
    if isinstance(result.get("failedElements"), list):
        extra["failedElements"] = await label_listed(result["failedElements"], port, warnings)
    if warnings:
        extra["warnings"] = warnings
    return {**result, **extra}


# The Archicad command behind each action, for read-only mode.
ACTION_COMMANDS = {
    _delete: "DeleteElements",
    _highlight: "HighlightElements",
    _set_property: "SetPropertyValuesOfElements",
    _move: "MoveElements",
    _rotate: "RotateElements",
    _set_details: "SetDetailsOfElements",
}


class _GoAhead(BaseModel):
    """The user's answer when asked before a change: accepting the question is the go-ahead."""


def _client_can_ask(ctx: Context) -> bool:
    """True when the client can show the user a question (MCP form elicitation)."""
    elicitation = ctx.client_capabilities.elicitation if ctx.client_capabilities else None
    # A client from before elicitation modes declares the capability empty, which means forms.
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


@dataclass
class _Checked:
    """The check before the change found nothing to ask the user about; what it found goes into the result."""

    found: dict[str, Any]


async def _ask_user(
    target_set: str | list[Any], action: str, port: int | None, dry_run: bool, ctx: Context
) -> Elicit[_GoAhead] | _Checked | None:
    """Ask the user directly before a change that needs their go-ahead, instead of trusting the AI's confirm=true.

    None: nothing was checked, because the client cannot ask or the call changes nothing it would ask about.
    """
    if not _client_can_ask(ctx):
        return None
    run_action = MUTATION_ACTIONS.get(action.strip().lower())
    if dry_run or run_action is None or run_action is _highlight:
        return None
    try:
        ensure_allowed(ACTION_COMMANDS[run_action], can_preview=True)
        ids, _ = element_ids(await target_elements(target_set, port or 19723))
    except ToolError:
        # archicad_mutate raises the same error itself, so there is nothing to ask about.
        return None
    if not ids:
        return None
    reasons, found = await confirmation_reasons(ids, port or 19723, deletes=run_action is _delete)
    if reasons is None:
        return _Checked(found)
    elements = f"{len(ids)} element{'s' if len(ids) != 1 else ''}"
    return Elicit(f"The AI wants to {action.strip().lower()} {elements} in Archicad. {reasons} Go ahead?", _GoAhead)


# What _ask_user found: the user's answer, or a plain value when nobody was asked.
UserAnswer = AcceptedElicitation[Any] | DeclinedElicitation | CancelledElicitation | None


def _confirmed(user_answer: UserAnswer, confirm: bool) -> tuple[bool, dict[str, Any]] | None:
    """Whether the change goes ahead without its own check, and what an earlier check found; None when the
    user said no."""
    if isinstance(user_answer, DeclinedElicitation | CancelledElicitation):
        return None
    data = user_answer.data if user_answer is not None else None
    if isinstance(data, _Checked):
        return True, data.found
    if isinstance(data, _GoAhead):
        return True, {}
    # The client cannot ask its user, so the AI's confirm decides.
    return confirm, {}


DECLINED_BY_USER = {
    "changed": False,
    "declinedByUser": True,
    "message": "Nothing was changed: the user said no when asked in their client. Do not repeat this change "
    "unless the user asks for it again.",
}


@mcp.tool(
    name="archicad_mutate",
    title="Mutate Archicad Elements",
    description=(
        "Changes or deletes the elements of a set, as one Undo step per call. "
        "target_set: a setId from archicad_query, or a list of elements. "
        "action and payload: 'set_property' {'propertyId', 'propertyValue'} or {'propertyValues': [...]}; "
        "'move' {'vector': {'x','y','z'}}; 'rotate' {'rotation': {...}}; 'set_details' {'details': {...}}; "
        "'highlight' {'highlightedColors', 'wireframe3D'}; 'delete' {}. "
        "A set does not follow later changes in Archicad: results give its age in 'setAgeSeconds' and how many "
        "of its elements no longer exist in 'missingCount'. Before changing anything, elements that no longer "
        "exist or cannot be changed (locked layer, or not reserved in Teamwork) are looked for; if there are "
        "any, nothing is changed and they are reported instead, and a delete that would also remove doors, "
        "windows, skylights or openings of other elements is held back the same way. Show that report to the "
        "user and repeat the call with confirm=true only if they agree. In a client that can ask its user "
        "directly, Caracara asks them itself before such a change and ignores confirm. Elements Archicad still "
        "skips are listed in 'failedElements'. dry_run=true changes nothing and returns the same report."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def archicad_mutate(
    target_set: str | list[Any],
    action: str,
    payload: dict[str, Any] | None = None,
    port: int | None = None,
    dry_run: bool = False,
    confirm: bool = False,
    user_answer: Annotated[UserAnswer, Resolve(_ask_user)] = None,
) -> dict[str, Any]:
    """Execute a polymorphic mutation on Archicad elements."""
    action = action.strip().lower()
    run_action = MUTATION_ACTIONS.get(action)
    if run_action is None:
        raise ToolError(
            f"Unsupported action '{action}'. "
            "Supported actions: 'delete', 'highlight', 'set_property', 'move', 'rotate', 'set_details'."
        )
    if not dry_run:
        ensure_allowed(ACTION_COMMANDS[run_action], can_preview=True)
    target_port = port or 19723
    elements = await target_elements(target_set, target_port)
    # An explicit element list gets a set of its own, so set-based commands can address it.
    # Its origin is unknown, so it is not tied to a project.
    set_id = target_set if isinstance(target_set, str) else ResultRegistry.register(elements)
    ids, invalid = element_ids(elements)
    if dry_run:
        report = await preflight(
            action,
            ids,
            invalid,
            target_port,
            changes_model=run_action is not _highlight,
            deletes=run_action is _delete,
        )
        return {"dryRun": True, **report, **_set_age(target_set)}
    decision = _confirmed(user_answer, confirm)
    if decision is None:
        return {**DECLINED_BY_USER}
    confirm, found = decision
    mutation = _Mutation(ids, invalid, set_id, payload, target_port, confirm)
    if run_action is _highlight:
        return await run_action(mutation)
    checked = _set_age(target_set) | found
    if not confirm and ids:
        report, warnings = await skipped_elements(ids, target_port)
        if held := _held_for_skipped(report, warnings, len(ids)):
            return {**held, **checked}
        checked |= {key: report[key] for key in ("missingCount",) if key in report} | {"warnings": warnings}
    return await _with_check_results(await run_action(mutation), target_port, checked)
