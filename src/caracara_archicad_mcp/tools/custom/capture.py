"""archicad_capture_view: lets the model see the model, when the user allows it.

A picture of a design is more sensitive than the text Caracara otherwise returns: it goes to
the AI provider like everything else a tool returns. So this tool is off unless the user
switches it on (--allow-images, CARACARA_MCP_ALLOW_IMAGES, or the setting in the .mcpb
bundle). When it is off the server does not even offer the tool.

The window capture is written by Archicad to a temporary file inside the output directory,
read back and deleted at once. An element preview comes back from Archicad in memory.
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image
from mcp.types import ImageContent, ToolAnnotations

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.file_paths import confine_path
from caracara_archicad_mcp.images import images_allowed
from caracara_archicad_mcp.tools.custom.element_sets import element_id
from caracara_archicad_mcp.tools.custom.functions import call_and_wait, call_and_wait_result

TOOL_NAME = "archicad_capture_view"
# Larger images are scaled down by the model anyway; this keeps previews within that.
MAX_PREVIEW_SIZE = 1568
# Window captures follow the screen size; refuse ones too large to send.
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _checked(data: bytes) -> bytes:
    if not data:
        raise ToolError("Archicad returned an empty image.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ToolError(
            f"The image is {len(data) // 1024} KB, more than the {MAX_IMAGE_BYTES // 1024} KB limit. "
            "Make the Archicad window smaller, or capture a single element instead."
        )
    return data


async def _window_image(port: int, entire_drawing: bool) -> bytes:
    # Only the file asked for is read and deleted, never a path taken from the reply.
    path = Path(confine_path(f"captures/{uuid.uuid4().hex}.jpg"))
    params: dict[str, Any] = {"imageFilePath": str(path), "format": "jpg"}
    if entire_drawing:
        params["entireDrawing"] = True
    result = await call_and_wait("project_save_current_window_as_image", {"port": port, "params": params})
    if not isinstance(result, dict) or not result.get("imageFilePath"):
        path.unlink(missing_ok=True)
        raise ToolError(f"Archicad did not save the window image: {result}")
    try:
        return _checked(path.read_bytes())
    except OSError as error:
        raise ToolError(f"The window image could not be read: {error}") from None
    finally:
        path.unlink(missing_ok=True)


async def _element_image(port: int, element: Any, view: str, size: int) -> bytes:
    elem_id = element_id(element)
    if elem_id is None:
        raise ToolError("'element' must be an element identifier such as {'guid': '...'}.")
    side = max(64, min(size, MAX_PREVIEW_SIZE))
    response = await call_and_wait_result(
        "elements_get_element_preview_image",
        {
            "port": port,
            "params": {"elementId": elem_id, "imageType": view, "format": "jpg", "width": side, "height": side},
        },
    )
    # The command's image field is moved out of the JSON into image content.
    images = [block for block in response.content if isinstance(block, ImageContent)]
    if images:
        return _checked(base64.b64decode(images[0].data))
    preview = (response.structured_content or {}).get("previewImage")
    if not preview:
        raise ToolError(f"Archicad returned no preview image: {response.structured_content}")
    return _checked(base64.b64decode(preview))


@mcp.tool(
    name=TOOL_NAME,
    title="Capture an Archicad View",
    description=(
        "Returns a picture so you can check your work visually. Without 'element' it captures the Archicad "
        "window that is currently in front (floor plan, 3D, section, ...); entire_drawing=true covers the "
        "whole drawing of a 2D window instead of its current zoom, and changes nothing in a 3D window. With "
        "'element' ({'guid': ...}) it returns a preview of that one element: view '3D' (default), '2D' or "
        "'Section', size in pixels (default 768). Pictures are for context only, not for judging exact sizes "
        "or positions; read those with archicad_query or archicad_calculate. "
        "Pictures of the design are sent to the AI provider; the user enabled this tool for that purpose."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_capture_view(
    port: int | None = None,
    element: dict[str, Any] | None = None,
    view: Literal["3D", "2D", "Section"] = "3D",
    size: int = 768,
    entire_drawing: bool = False,
) -> Image:
    if not images_allowed():
        raise ToolError("Sending images is switched off. Only the user can switch it on, in the server settings.")
    target_port = port or 19723
    if element is not None:
        data = await _element_image(target_port, element, view, size)
    else:
        data = await _window_image(target_port, entire_drawing)
    return Image(data=data, format="jpeg")
