"""The user's settings: read-only mode, images and the commands that close the project.

server.py sets each one from its command-line option. Until it does (in tests, or when another
program imports the package) each falls back to its CARACARA_MCP_ environment variable, or the
TAPIR_MCP_ one kept for configurations written before the rename.
"""

from __future__ import annotations

import os

_read_only: bool | None = None
_images_allowed: bool | None = None
_session_commands_allowed: bool | None = None


def env(name: str, default: str | None = None) -> str | None:
    """The CARACARA_MCP_<name> environment variable, else TAPIR_MCP_<name>, else default."""
    # An MCP bundle passes an option the user left empty as an empty string, which counts as unset.
    return os.environ.get(f"CARACARA_MCP_{name}") or os.environ.get(f"TAPIR_MCP_{name}") or default


def env_flag(name: str) -> bool:
    """True when the switch's environment variable says yes."""
    return (env(name) or "").strip().lower() in ("1", "true", "yes")


def set_read_only(enabled: bool | None) -> None:
    """Turn read-only mode on or off (None falls back to the environment)."""
    global _read_only
    _read_only = enabled


def read_only() -> bool:
    return env_flag("READ_ONLY") if _read_only is None else _read_only


def set_images_allowed(allowed: bool | None) -> None:
    """Allow or forbid sending images (None falls back to the environment)."""
    global _images_allowed
    _images_allowed = allowed


def images_allowed() -> bool:
    return env_flag("ALLOW_IMAGES") if _images_allowed is None else _images_allowed


def set_session_commands_allowed(allowed: bool | None) -> None:
    """Allow or forbid the commands that close or replace the open project (None falls back to the environment)."""
    global _session_commands_allowed
    _session_commands_allowed = allowed


def session_commands_allowed() -> bool:
    return env_flag("ALLOW_SESSION_COMMANDS") if _session_commands_allowed is None else _session_commands_allowed
