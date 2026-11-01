"""archicad_capture_view: off unless allowed, and it only ever touches its own temporary file."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp import file_paths
from caracara_archicad_mcp.settings import images_allowed, set_images_allowed
from caracara_archicad_mcp.tools.custom import capture
from caracara_archicad_mcp.tools.custom.capture import archicad_capture_view

JPEG = b"\xff\xd8\xff\xe0 fake jpeg bytes"
GUID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    monkeypatch.delenv("CARACARA_MCP_ALLOW_IMAGES", raising=False)
    monkeypatch.delenv("TAPIR_MCP_ALLOW_IMAGES", raising=False)
    file_paths.set_output_root(tmp_path / "output")
    set_images_allowed(None)
    yield
    set_images_allowed(None)
    file_paths.set_output_root(None)


@pytest.fixture
def window(fake_archicad):
    """Archicad writes the requested image file and answers with a path of its own choosing."""
    requested: list[Path] = []

    def save(params):
        path = Path(params["imageFilePath"])
        requested.append(path)
        path.write_bytes(JPEG)
        return {"imageFilePath": params["imageFilePath"]}

    fake_archicad.on_tapir_command("SaveCurrentWindowAsImage", save)
    return requested


def test_images_are_off_by_default():
    assert not images_allowed()


@pytest.mark.parametrize("value", ["1", "true", "Yes"])
def test_environment_allows_images(monkeypatch, value):
    monkeypatch.setenv("CARACARA_MCP_ALLOW_IMAGES", value)
    assert images_allowed()


@pytest.mark.asyncio
async def test_refuses_when_images_are_off(fake_archicad):
    with pytest.raises(ToolError, match="switched off"):
        await archicad_capture_view(port=fake_archicad.port)
    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_window_capture_returns_the_picture_and_deletes_the_file(fake_archicad, window, tmp_path):
    set_images_allowed(True)

    image = await archicad_capture_view(port=fake_archicad.port)

    content = image.to_image_content()
    assert base64.b64decode(content.data) == JPEG
    assert content.mime_type == "image/jpeg"
    assert len(window) == 1
    assert window[0].is_relative_to(tmp_path / "output")
    assert not window[0].exists()
    assert fake_archicad.calls[0][1]["format"] == "jpg"
    assert "entireDrawing" not in fake_archicad.calls[0][1]


@pytest.mark.asyncio
async def test_only_the_requested_file_is_read_and_deleted(fake_archicad, tmp_path):
    set_images_allowed(True)
    bystander = tmp_path / "someone_elses.jpg"
    bystander.write_bytes(b"do not touch")

    def save(params):
        Path(params["imageFilePath"]).write_bytes(JPEG)
        return {"imageFilePath": str(bystander)}

    fake_archicad.on_tapir_command("SaveCurrentWindowAsImage", save)

    image = await archicad_capture_view(port=fake_archicad.port)

    assert base64.b64decode(image.to_image_content().data) == JPEG
    assert bystander.read_bytes() == b"do not touch"


@pytest.mark.asyncio
async def test_entire_drawing_is_passed_on(fake_archicad, window):
    set_images_allowed(True)
    await archicad_capture_view(port=fake_archicad.port, entire_drawing=True)
    assert fake_archicad.calls[0][1]["entireDrawing"] is True


@pytest.mark.asyncio
async def test_oversized_images_are_refused_and_still_deleted(fake_archicad, window, monkeypatch):
    set_images_allowed(True)
    monkeypatch.setattr(capture, "MAX_IMAGE_BYTES", 4)
    with pytest.raises(ToolError, match="limit"):
        await archicad_capture_view(port=fake_archicad.port)
    assert not window[0].exists()


@pytest.mark.asyncio
async def test_element_preview_comes_from_memory(fake_archicad):
    set_images_allowed(True)
    fake_archicad.on_tapir_command("GetElementPreviewImage", {"previewImage": base64.b64encode(JPEG).decode()})

    image = await archicad_capture_view(port=fake_archicad.port, element={"guid": GUID}, view="Section", size=5000)

    assert base64.b64decode(image.to_image_content().data) == JPEG
    command, params = fake_archicad.calls[0]
    assert command == "GetElementPreviewImage"
    assert params == {
        "elementId": {"guid": GUID},
        "imageType": "Section",
        "format": "jpg",
        "width": capture.MAX_PREVIEW_SIZE,
        "height": capture.MAX_PREVIEW_SIZE,
    }


@pytest.mark.asyncio
async def test_element_must_be_an_identifier(fake_archicad):
    set_images_allowed(True)
    with pytest.raises(ToolError, match="element identifier"):
        await archicad_capture_view(port=fake_archicad.port, element={"name": "wall"})


def test_server_offers_the_tool_only_when_allowed(monkeypatch):
    from caracara_archicad_mcp import server

    remove = Mock()
    monkeypatch.setattr(server.mcp, "remove_tool", remove)

    server.configure_images(False)
    remove.assert_called_once_with(capture.TOOL_NAME)
    assert not images_allowed()

    remove.reset_mock()
    server.configure_images(True)
    remove.assert_not_called()
    assert images_allowed()


def test_server_flag_allows_images(monkeypatch):
    from caracara_archicad_mcp.server import parse_args

    monkeypatch.setattr(sys, "argv", ["server.py", "--allow-images"])
    assert parse_args().allow_images
    monkeypatch.setattr(sys, "argv", ["server.py"])
    assert not parse_args().allow_images


def test_description_keeps_pictures_to_context():
    """Sizes and positions read from a picture are guesses; the description points to exact readings."""
    from caracara_archicad_mcp.app import mcp

    tool = mcp._tool_manager.get_tool(capture.TOOL_NAME)
    assert "context only" in tool.description
    assert "archicad_calculate" in tool.description
