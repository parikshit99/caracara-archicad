"""Read-only mode: the user's switch that keeps the AI from changing anything.

When it is on, every Archicad command that is not known to only read is refused
before it reaches Archicad. Commands are judged by name against an allowlist, so a
command added by a future Tapir release is refused until it is reviewed here.
"""

from __future__ import annotations

import os

from mcp.server.mcpserver.exceptions import ToolError

# Commands starting with these verbs only read the model.
READ_ONLY_PREFIXES = ("Get", "Is", "Filter")
# Commands that change only what is shown on screen, never the model or files.
DISPLAY_ONLY_COMMANDS = frozenset({"HighlightElements", "ChangeSelectionOfElements", "FitInWindow", "RebuildView"})
# Commands that write a picture of the Archicad window into the server's output directory and change
# nothing in the project, so screenshots still work in read-only mode.
WINDOW_IMAGE_COMMANDS = frozenset({"SaveCurrentWindowAsImage"})
# Commands allowed in read-only mode besides the reading verbs, each reviewed by name.
REVIEWED_COMMANDS = DISPLAY_ONLY_COMMANDS | WINDOW_IMAGE_COMMANDS

# Commands that end the open project and discard whatever was not saved. Archicad cannot report unsaved
# changes, so they stay refused until the user allows them.
SESSION_COMMANDS = frozenset({"CloseProject", "NewProject"})

_read_only: bool | None = None
_session_commands_allowed: bool | None = None


def set_read_only(enabled: bool | None) -> None:
    """Turn read-only mode on or off (None falls back to the environment)."""
    global _read_only
    _read_only = enabled


def read_only() -> bool:
    if _read_only is not None:
        return _read_only
    value = os.environ.get("CARACARA_MCP_READ_ONLY") or os.environ.get("TAPIR_MCP_READ_ONLY") or ""
    return value.strip().lower() in ("1", "true", "yes")


def set_session_commands_allowed(allowed: bool | None) -> None:
    """Allow or forbid the commands that close or replace the open project (None falls back to the environment)."""
    global _session_commands_allowed
    _session_commands_allowed = allowed


def session_commands_allowed() -> bool:
    if _session_commands_allowed is not None:
        return _session_commands_allowed
    value = os.environ.get("CARACARA_MCP_ALLOW_SESSION_COMMANDS") or ""
    return value.strip().lower() in ("1", "true", "yes")


def is_reading_command(command: str) -> bool:
    """True when the Archicad command (e.g. 'GetLayers', 'API.GetAllElements') only reads the model."""
    name = command.removeprefix("API.")
    # The verb must end at a word boundary: 'GetLayers' and 'Get3DBoundingBoxes' read, 'Getaway' would not.
    return any(
        name.startswith(prefix) and len(name) > len(prefix) and not name[len(prefix)].islower()
        for prefix in READ_ONLY_PREFIXES
    )


def is_read_only_command(command: str) -> bool:
    """True when the Archicad command cannot change the project: a reading command or a reviewed one."""
    return command.removeprefix("API.") in REVIEWED_COMMANDS or is_reading_command(command)


def ensure_allowed(command: str, *, can_preview: bool = False) -> None:
    """Refuse a command that could change the model, files or Teamwork state while in read-only mode.

    can_preview: the command is an archicad_mutate action, which a dry run can preview instead.
    """
    if command.removeprefix("API.") in SESSION_COMMANDS and not session_commands_allowed():
        raise ToolError(
            f"'{command}' closes or replaces the open project and Archicad discards unsaved changes, so it is "
            "switched off. Only the user can allow it, with --allow-session-commands or the matching setting. "
            "Ask the user to save the project and close or create it themselves."
        )
    if read_only() and not is_read_only_command(command):
        preview = " To preview this change, call archicad_mutate with dry_run=true." if can_preview else ""
        raise ToolError(
            f"'{command}' can change the project or write files, and this server runs in read-only mode, which "
            f"the user switched on. Only reading is allowed.{preview}"
        )
