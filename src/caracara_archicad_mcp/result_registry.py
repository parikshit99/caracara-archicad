"""Local Virtual Set Registry for Archicad elements.

Holds active element GUID collections in RAM and returns opaque set_id tokens.
Prevents transmitting raw GUID arrays to the LLM context.

Memory is bounded three ways: at most MAX_CAPACITY sets (LRU eviction), at most
MAX_TOTAL_ELEMENTS elements across all sets (LRU eviction), and an idle TTL after
which an unused set expires. All access is serialized by a lock because tools run
on worker threads.

A set remembers the port and project its elements were found in. A GUID means
something only in its own project, and a copy of a file keeps the GUIDs of the
original, so a set used on another port or after another project was opened
would quietly address the wrong elements; such a use is refused.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, ClassVar

from mcp.server.mcpserver.exceptions import ToolError

# Process-wide RAM storage for active element sets, ordered least to most recently used.
STORAGE_SETS: OrderedDict[str, list[dict]] = OrderedDict()

_MAX_PREFIX_LENGTH = 32
_MAX_PREFIXES = 256
UNTITLED = "untitled"


@dataclass(frozen=True)
class SetProject:
    """The Archicad (port) and project a set's elements belong to."""

    port: int
    project: str  # 'solo:<path>', 'teamwork:<location>' or 'untitled'

    def describe(self) -> str:
        name = "an unsaved project" if self.project == UNTITLED else self.project.split(":", 1)[1]
        return f"{name} on port {self.port}"


def project_identity(project_info: Any) -> str:
    """The open project, from Tapir's GetProjectInfo answer. All unsaved projects look the same."""
    if not isinstance(project_info, dict):
        raise ToolError(f"Archicad did not say which project is open: {project_info}")
    if project_info.get("isUntitled"):
        return UNTITLED
    location = project_info.get("projectLocation") or project_info.get("projectPath")
    if not location:
        raise ToolError("Archicad did not say where the open project is saved; try again.")
    return f"{'teamwork' if project_info.get('isTeamwork') else 'solo'}:{location}"


class ResultRegistry:
    """Registry holding element collections in RAM with opaque token handles and LRU eviction."""

    DEFAULT_MAX_CAPACITY: ClassVar[int] = 50
    MAX_CAPACITY: ClassVar[int] = 50
    DEFAULT_MAX_TOTAL_ELEMENTS: ClassVar[int] = 500_000
    MAX_TOTAL_ELEMENTS: ClassVar[int] = 500_000
    DEFAULT_TTL_SECONDS: ClassVar[float | None] = 3600.0
    TTL_SECONDS: ClassVar[float | None] = 3600.0

    _counters: ClassVar[dict[str, int]] = {}
    _timestamps: ClassVar[dict[str, float]] = {}
    # When each set was found; a set does not follow later changes in Archicad.
    _created: ClassVar[dict[str, float]] = {}
    _projects: ClassVar[dict[str, SetProject]] = {}
    _lock: ClassVar[threading.RLock] = threading.RLock()

    @classmethod
    def set_capacity(cls, capacity: int) -> None:
        """Set the maximum number of active sets retained in RAM."""
        if capacity < 1:
            raise ValueError("Capacity must be at least 1.")
        with cls._lock:
            cls.MAX_CAPACITY = capacity
            cls._evict_excess()

    @classmethod
    def set_max_total_elements(cls, max_elements: int) -> None:
        """Set the maximum number of elements retained across all sets."""
        if max_elements < 1:
            raise ValueError("Element budget must be at least 1.")
        with cls._lock:
            cls.MAX_TOTAL_ELEMENTS = max_elements
            cls._evict_excess()

    @classmethod
    def set_ttl(cls, ttl_seconds: float | None) -> None:
        """Set the idle time-to-live in seconds for sets (None disables TTL expiration)."""
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("TTL must be positive, or None to disable expiry.")
        with cls._lock:
            cls.TTL_SECONDS = ttl_seconds
            cls.evict_expired()

    @classmethod
    def reset_limits(cls) -> None:
        """Reset capacity, element budget and TTL limits to default values."""
        with cls._lock:
            cls.MAX_CAPACITY = cls.DEFAULT_MAX_CAPACITY
            cls.MAX_TOTAL_ELEMENTS = cls.DEFAULT_MAX_TOTAL_ELEMENTS
            cls.TTL_SECONDS = cls.DEFAULT_TTL_SECONDS

    @classmethod
    def register(
        cls, elements: list[dict | Any], element_type: str | None = None, project: SetProject | None = None
    ) -> str:
        """Register a list of element dictionaries and return an opaque set_id handle.

        Example set_id: 'set_walls_01', 'set_columns_02', 'set_elements_01'.
        project: where the elements were found; a set without one is not checked when used.
        Evicts least-recently-used sets until the new set fits the capacity and element budget.
        """
        normalized = [cls._normalize(elem) for elem in elements]
        if len(normalized) > cls.MAX_TOTAL_ELEMENTS:
            raise ToolError(
                f"Result of {len(normalized)} elements exceeds the virtual set limit of "
                f"{cls.MAX_TOTAL_ELEMENTS}. Narrow the query with a filter or element type."
            )

        with cls._lock:
            cls.evict_expired()
            while STORAGE_SETS and (
                len(STORAGE_SETS) >= cls.MAX_CAPACITY
                or cls._total_elements() + len(normalized) > cls.MAX_TOTAL_ELEMENTS
            ):
                cls._delete_unlocked(next(iter(STORAGE_SETS)))

            prefix = cls._format_prefix(element_type)
            if prefix != "elements" and prefix not in cls._counters and len(cls._counters) >= _MAX_PREFIXES - 1:
                prefix = "elements"
            count = cls._counters.get(prefix, 0) + 1
            cls._counters[prefix] = count
            set_id = f"set_{prefix}_{count:02d}"

            STORAGE_SETS[set_id] = normalized
            cls._timestamps[set_id] = cls._created[set_id] = time.monotonic()
            if project is not None:
                cls._projects[set_id] = project
            return set_id

    @classmethod
    def get(cls, set_id: str) -> list[dict]:
        """Retrieve the element collection for a given set_id.

        Marks the set as most recently used (LRU update) and refreshes its idle TTL.
        Raises KeyError if the set_id is not found or has expired.
        """
        with cls._lock:
            if not cls._alive(set_id):
                raise KeyError(f"Set handle '{set_id}' not found or expired.")
            STORAGE_SETS.move_to_end(set_id)
            cls._timestamps[set_id] = time.monotonic()
            return STORAGE_SETS[set_id]

    @classmethod
    def get_or_tool_error(cls, set_id: str, project: SetProject | None = None) -> list[dict]:
        """Retrieve the element collection for a given set_id, raising ToolError on failure.

        project: the port and project the set is about to be used in; a set found elsewhere is refused.
        """
        with cls._lock:
            try:
                elements = cls.get(set_id)
            except KeyError:
                raise ToolError(f"Set handle '{set_id}' not found or expired.") from None
            found_in = cls._projects.get(set_id)
        if project is not None and found_in is not None and found_in != project:
            raise ToolError(
                f"Set '{set_id}' was found in {found_in.describe()}, but this call works in {project.describe()}. "
                "Its elements belong to the other project; query again in this one."
            )
        return elements

    @classmethod
    def has(cls, set_id: str) -> bool:
        """Check if a set_id exists in the registry and has not expired."""
        with cls._lock:
            return cls._alive(set_id)

    @classmethod
    def delete(cls, set_id: str) -> None:
        """Remove a set_id from the registry."""
        with cls._lock:
            cls._delete_unlocked(set_id)

    @classmethod
    def clear(cls) -> None:
        """Clear all active sets and reset counters and timestamps."""
        with cls._lock:
            STORAGE_SETS.clear()
            cls._counters.clear()
            cls._timestamps.clear()
            cls._created.clear()
            cls._projects.clear()

    @classmethod
    def project_of(cls, set_id: str) -> SetProject | None:
        """Where the set's elements were found, or None when that is unknown."""
        with cls._lock:
            return cls._projects.get(set_id)

    @classmethod
    def age_seconds(cls, set_id: str) -> float | None:
        """Seconds since the set was found, or None for an unknown or expired set."""
        with cls._lock:
            if not cls._alive(set_id):
                return None
            return time.monotonic() - cls._created[set_id]

    @classmethod
    def count(cls, set_id: str) -> int:
        """Return the number of elements in a set."""
        return len(cls.get(set_id))

    @classmethod
    def evict_expired(cls) -> int:
        """Evict all sets idle for longer than TTL_SECONDS. Returns number of evicted sets."""
        if cls.TTL_SECONDS is None:
            return 0
        with cls._lock:
            now = time.monotonic()
            expired_keys = [sid for sid, ts in cls._timestamps.items() if now - ts > cls.TTL_SECONDS]
            for sid in expired_keys:
                cls._delete_unlocked(sid)
            return len(expired_keys)

    @classmethod
    def _alive(cls, set_id: str) -> bool:
        if set_id not in STORAGE_SETS:
            return False
        if cls.TTL_SECONDS is not None and time.monotonic() - cls._timestamps.get(set_id, 0.0) > cls.TTL_SECONDS:
            cls._delete_unlocked(set_id)
            return False
        return True

    @classmethod
    def _delete_unlocked(cls, set_id: str) -> None:
        STORAGE_SETS.pop(set_id, None)
        cls._timestamps.pop(set_id, None)
        cls._created.pop(set_id, None)
        cls._projects.pop(set_id, None)

    @classmethod
    def _total_elements(cls) -> int:
        return sum(len(elements) for elements in STORAGE_SETS.values())

    @classmethod
    def _evict_excess(cls) -> int:
        """Evict least recently used sets while over the set capacity or element budget."""
        evicted = 0
        while STORAGE_SETS and (len(STORAGE_SETS) > cls.MAX_CAPACITY or cls._total_elements() > cls.MAX_TOTAL_ELEMENTS):
            cls._delete_unlocked(next(iter(STORAGE_SETS)))
            evicted += 1
        return evicted

    @staticmethod
    def _normalize(elem: Any) -> dict:
        if isinstance(elem, dict):
            return elem
        if hasattr(elem, "model_dump"):
            d = elem.model_dump(by_alias=True)
            if "element_id" in d and "elementId" not in d:
                d["elementId"] = d.pop("element_id")
            return d
        return {"elementId": {"guid": str(elem)}}

    @classmethod
    def _format_prefix(cls, element_type: Any | None) -> str:
        """Normalize an element type name into a clean pluralized snake_case token prefix."""
        if element_type is None:
            return "elements"
        raw_str = str(element_type.value) if hasattr(element_type, "value") else str(element_type)
        cleaned = re.sub(r"[^a-zA-Z0-9_]", "", raw_str).lower()[:_MAX_PREFIX_LENGTH]
        if not cleaned:
            return "elements"
        if cleaned in ("selected", "filtered", "classified", "all"):
            return "elements" if cleaned == "all" else cleaned
        if not cleaned.endswith("s"):
            return f"{cleaned}s"
        return cleaned
