"""Confinement of file paths that Archicad commands write to.

Commands that make Archicad write a file (exports, archives, screenshots, Save As)
take an OS path from the caller. Those paths are resolved with pathlib and must
land inside one dedicated output root; relative paths are placed under it.
Paths that Archicad only reads (opening a project, importing BCF) are not
confined, since they must reach the user's existing files.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad.models.tapir.commands import (
    ExportFavoritesParameters,
    ExportIssuesToBCFParameters,
    GenerateDocumentationParameters,
    IFCFileOperationParameters,
    PublishPublisherSetParameters,
    SaveAsModuleFileParameters,
    SaveCurrentWindowAsImageParameters,
    SaveProjectAsArchiveParameters,
    SaveProjectParameters,
)
from multiconn_archicad.models.tapir.types import Method

from caracara_archicad_mcp.settings import env

DEFAULT_OUTPUT_ROOT = Path(tempfile.gettempdir()) / "caracara-archicad-mcp"

_output_root: Path | None = None


def _always(_params: Any) -> bool:
    return True


# Parameter model -> (path field, predicate deciding whether this call writes).
WRITE_PATH_FIELDS: dict[type, tuple[str, Callable[[Any], bool]]] = {
    ExportFavoritesParameters: ("path", _always),
    ExportIssuesToBCFParameters: ("exportPath", _always),
    GenerateDocumentationParameters: ("destinationFolder", _always),
    PublishPublisherSetParameters: ("outputPath", _always),
    SaveAsModuleFileParameters: ("moduleFilePath", _always),
    SaveCurrentWindowAsImageParameters: ("imageFilePath", _always),
    SaveProjectAsArchiveParameters: ("archiveFilePath", _always),
    SaveProjectParameters: ("projectPath", _always),
    IFCFileOperationParameters: ("ifcFilePath", lambda params: params.method == Method.SAVE),
}


def set_output_root(root: str | os.PathLike | None) -> None:
    """Set the directory Archicad file writes are confined to (None restores the default)."""
    global _output_root
    _output_root = Path(root).expanduser().resolve() if root else None


def output_root() -> Path:
    root = _output_root
    if root is None:
        env_root = env("OUTPUT_ROOT")
        root = Path(env_root).expanduser() if env_root else DEFAULT_OUTPUT_ROOT
    return root.resolve()


def confine_path(raw_path: str) -> str:
    """Resolve raw_path against the output root and reject anything that escapes it."""
    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        raise ToolError("Invalid file path.")
    root = output_root()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    if resolved != root and not resolved.is_relative_to(root):
        raise ToolError(
            f"File path '{raw_path}' is outside the allowed output directory '{root}'. "
            "Use a path inside it, or a relative path to place the file there."
        )
    # Archicad does not create missing folders, so make the target's parent (always inside root).
    root.mkdir(parents=True, exist_ok=True)
    if resolved != root:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def confine_write_paths(params: Any) -> Any:
    """Return params with any Archicad write path confined to the output root."""
    for model, (field, writes) in WRITE_PATH_FIELDS.items():
        if isinstance(params, model):
            raw_path = getattr(params, field)
            if raw_path is None or not writes(params):
                return params
            return params.model_copy(update={field: confine_path(raw_path)})
    return params
