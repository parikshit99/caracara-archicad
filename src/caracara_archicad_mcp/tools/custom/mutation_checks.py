"""Read-only checks of what archicad_mutate is about to do, run before anything changes.

Archicad skips elements that no longer exist or that it may not change (locked layer, or not
reserved by this user in Teamwork), and deletes the doors, windows, skylights and openings of
a deleted host with it. These checks find both in advance, so a dry run can report them and a
change that would skip elements or remove hosted ones can wait for the user's confirmation.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom.element_sets import as_elements, element_id, element_labels, labels_or_warning
from caracara_archicad_mcp.tools.custom.functions import call_tool_payload, is_running

# Element types Archicad deletes together with the element that hosts them.
HOSTED_ELEMENT_TYPES = ("Door", "Window", "Skylight", "Opening")
# Elements itemized in a report; the rest are only counted.
MAX_LISTED = 50


def _guids(elements: list[Any]) -> list[str]:
    return [elem_id["guid"] for elem in elements if (elem_id := element_id(elem)) is not None]


async def not_editable(element_ids: list[dict[str, Any]], port: int) -> tuple[list[dict[str, Any]], str | None]:
    """The elements Archicad would refuse to change, and a warning when that could not be checked."""
    try:
        result = await call_tool_payload(
            "elements_filter_elements",
            {"port": port, "params": {"elements": as_elements(element_ids), "filters": ["IsEditable"]}},
        )
    except ToolError as error:
        return [], f"Locked elements were not checked: {error}"
    if is_running(result):
        return [], "The editability check is still running in Archicad, so locked elements were not checked."
    set_id = result.get("setId", "")
    if not ResultRegistry.has(set_id):
        return [], "Locked elements were not checked: Archicad gave no answer."
    editable = set(_guids(ResultRegistry.get(set_id)))
    ResultRegistry.delete(set_id)
    return [e for e in element_ids if e["guid"] not in editable], None


async def hosted_elements(element_ids: list[dict[str, Any]], port: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Doors, windows, skylights and openings outside the target that a delete would remove with their hosts,
    and the hosted types that could not be checked."""
    targets = {e["guid"] for e in element_ids}
    hosted: dict[str, str] = {}
    unchecked: list[str] = []
    for element_type in HOSTED_ELEMENT_TYPES:
        try:
            result = await call_tool_payload(
                "elements_get_connected_elements",
                {"port": port, "params": {"elements": as_elements(element_ids), "connectedElementType": element_type}},
            )
        except ToolError as error:
            unchecked.append(f"{element_type}: {error}")
            continue
        if is_running(result) or not isinstance(result.get("connectedElements"), list):
            unchecked.append(f"{element_type}: {result.get('error', {}).get('message', 'no answer from Archicad')}")
            continue
        for owner in result["connectedElements"]:
            for guid in _guids(owner.get("elements", []) if isinstance(owner, dict) else []):
                if guid not in targets:
                    hosted.setdefault(guid, element_type)
    return [{"elementId": {"guid": guid}, "type": kind} for guid, kind in hosted.items()], unchecked


async def skipped_elements(element_ids: list[dict[str, Any]], port: int) -> tuple[dict[str, Any], list[str]]:
    """The target elements Archicad would skip, split into missing ones (deleted after the set was found)
    and ones it may not change, each named by its Element ID, and warnings about what could not be checked."""
    skipped, unchecked = await not_editable(element_ids, port)
    if unchecked:
        return {}, [unchecked]
    warnings: list[str] = []
    labels: dict[str, str | None] | None
    try:
        labels = await element_labels([e["guid"] for e in skipped], port)
    except ToolError as error:
        labels = None
        warnings.append(f"Elements that no longer exist were not told apart from locked ones: {error}")
    gone = {guid for guid, label in (labels or {}).items() if label is None}
    missing = [e for e in skipped if e["guid"] in gone]
    locked = [e for e in skipped if e["guid"] not in gone]
    report: dict[str, Any] = {} if labels is None else {"missingCount": len(missing)}
    if missing:
        report["missing"] = as_elements(missing[:MAX_LISTED])
        warnings.append(
            f"{len(missing)} of {len(element_ids)} elements no longer exist in Archicad; they were deleted after "
            "the set was found. Archicad will skip them."
        )
    if locked:
        report["notEditableCount"] = len(locked)
        report["notEditable"] = with_labels(as_elements(locked[:MAX_LISTED]), labels or {})
        warnings.append(
            f"{len(locked)} of {len(element_ids)} elements cannot be changed: they are on a locked layer, "
            "or, in a Teamwork project, not reserved by you. Archicad will skip them."
        )
    return report, warnings


def with_labels(items: list[dict[str, Any]], labels: dict[str, str | None]) -> list[dict[str, Any]]:
    """Add the Element ID ('id') to listed elements whose ID is known."""
    named = []
    for item in items:
        label = labels.get(item.get("elementId", {}).get("guid", ""))
        named.append({**item, "id": label} if label else item)
    return named


async def label_listed(items: list[dict[str, Any]], port: int, warnings: list[str]) -> list[dict[str, Any]]:
    """Add the Element IDs to a short list of elements (entries without an element are kept as they are),
    or say why they are missing."""
    guids = [item["elementId"]["guid"] for item in items if "elementId" in item]
    return with_labels(items, await labels_or_warning(guids, port, warnings))


def describe_hosted(hosted: list[dict[str, Any]]) -> str:
    """'2 doors, 1 window'."""
    counts: dict[str, int] = {}
    for item in hosted:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    return ", ".join(f"{count} {kind.lower()}{'s' if count != 1 else ''}" for kind, count in counts.items())


async def confirmation_reasons(
    element_ids: list[dict[str, Any]], port: int, *, deletes: bool
) -> tuple[str | None, dict[str, Any]]:
    """Why a change needs the user's go-ahead (Archicad would skip some of its elements, or a delete would also
    remove doors, windows, skylights or openings), or None when it needs none; and the missing-element count
    and warnings the check found, for the change's result."""
    # The client may ask again with the same text on a later round and match the answer to it, so the text
    # holds only counts that stay the same while the model does.
    reasons: list[str] = []
    report, warnings = await skipped_elements(element_ids, port)
    found = {key: report[key] for key in ("missingCount",) if key in report} | {"warnings": warnings}
    skipped = report.get("missingCount", 0) + report.get("notEditableCount", 0)
    if skipped:
        reasons.append(
            f"Archicad would skip {skipped} of them: they no longer exist, are on a locked layer, or are not "
            "reserved by you in Teamwork."
        )
    if deletes:
        hosted, _ = await hosted_elements(element_ids, port)
        if hosted:
            reasons.append(f"Deleting them also deletes {describe_hosted(hosted)} placed in them.")
    return " ".join(reasons) or None, found


async def preflight(
    action: str,
    element_ids: list[dict[str, Any]],
    invalid: list[dict[str, Any]],
    port: int,
    *,
    changes_model: bool,
    deletes: bool,
) -> dict[str, Any]:
    """What a mutation would affect, without changing anything. Highlighting changes no model data."""
    report: dict[str, Any] = {"action": action, "elementCount": len(element_ids)}
    warnings: list[str] = []
    if invalid:
        report["invalidEntries"] = invalid[:MAX_LISTED]
        warnings.append(f"{len(invalid)} entries have no element GUID and will be skipped.")

    if element_ids and changes_model:
        skipped, skipped_warnings = await skipped_elements(element_ids, port)
        report.update(skipped)
        warnings.extend(skipped_warnings)

    if element_ids and deletes:
        hosted, unchecked_types = await hosted_elements(element_ids, port)
        report["hostedElementCount"] = len(hosted)
        if hosted:
            report["hostedElements"] = await label_listed(hosted[:MAX_LISTED], port, warnings)
            warnings.append(f"Deleting these elements also deletes {describe_hosted(hosted)} placed in them.")
        if unchecked_types:
            report["uncheckedHostedTypes"] = unchecked_types

    report["warnings"] = warnings
    return report
