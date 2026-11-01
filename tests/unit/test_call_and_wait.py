"""call_and_wait: the answer itself, also when Archicad needs longer than the async threshold."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent

from caracara_archicad_mcp.images import build_result
from caracara_archicad_mcp.tools.custom import functions
from caracara_archicad_mcp.tools.custom.functions import call_and_wait, is_running

RUNNING = {"status": "running", "jobHandle": "job:1"}


@pytest.fixture
def archicad(monkeypatch):
    """archicad_call_tool answers 'running'; archicad_get_job answers from the given list, in order."""
    polls: list[str] = []
    answers: list[dict] = []

    async def call_tool(name, arguments):
        return build_result(RUNNING)

    async def get_job(handle):
        polls.append(handle)
        return build_result(answers.pop(0))

    monkeypatch.setattr(functions, "archicad_call_tool", call_tool)
    monkeypatch.setattr(functions, "archicad_get_job", get_job)
    return polls, answers


def test_is_running():
    assert is_running(RUNNING)
    assert not is_running({"status": "running"})
    assert not is_running({"stories": []})
    assert not is_running(None)


@pytest.mark.asyncio
async def test_waits_until_archicad_has_finished(archicad):
    polls, answers = archicad
    answers += [{"status": "running"}, {"status": "completed", "result": {"stories": []}}]

    assert await call_and_wait("project_get_stories", {"port": 19723}) == {"stories": []}
    assert polls == ["job:1", "job:1"]


@pytest.mark.asyncio
async def test_reports_when_archicad_fails(archicad):
    _, answers = archicad
    answers.append({"status": "failed", "error": {"message": "Archicad closed"}})

    with pytest.raises(ToolError, match="Archicad closed"):
        await call_and_wait("project_get_stories", {"port": 19723})


@pytest.mark.asyncio
async def test_gives_up_after_the_limit(archicad):
    _, answers = archicad
    answers.append({"status": "running"})

    with pytest.raises(ToolError, match="still working"):
        await call_and_wait("project_get_stories", {"port": 19723}, limit_seconds=-1)


@pytest.mark.asyncio
async def test_collect_follows_every_page(monkeypatch):
    pages = {
        None: {"attributes": [1, 2], "next_page_token": "p2"},
        "p2": {"attributes": [3], "next_page_token": "p3"},
        "p3": {"attributes": [4]},
    }

    async def call_tool(name, arguments):
        return build_result(pages[arguments.get("page_token")])

    monkeypatch.setattr(functions, "archicad_call_tool", call_tool)

    assert await functions.call_and_collect("attributes_get_attributes_by_type", {"port": 1}, "attributes") == [
        1,
        2,
        3,
        4,
    ]


@pytest.mark.asyncio
async def test_collect_needs_the_field(monkeypatch):
    async def call_tool(name, arguments):
        return build_result({"error": {"message": "unavailable"}})

    monkeypatch.setattr(functions, "archicad_call_tool", call_tool)

    with pytest.raises(ToolError, match="no 'attributes'"):
        await functions.call_and_collect("attributes_get_attributes_by_type", {"port": 1}, "attributes")


@pytest.mark.asyncio
async def test_finished_job_keeps_its_image(archicad, monkeypatch):
    _, answers = archicad
    image = ImageContent(type="image", data="AAAA", mime_type="image/png")
    result = {"previewImage": "returned as MCP image content #1"}
    answers.append({"status": "completed", "result": result})

    async def get_job(handle):
        return build_result(answers.pop(0), [image])

    monkeypatch.setattr(functions, "archicad_get_job", get_job)
    response = await functions.call_and_wait_result("elements_get_element_preview_image", {"port": 19723})

    assert response.structured_content == result
    assert response.content[1:] == [image]
