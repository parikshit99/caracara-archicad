# these are needed to ensure configuration happens before models are loaded
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from multiconn_archicad import ArchicadLocation, Port, ProductInfo, SoloProjectID

import caracara_archicad_mcp.model_configuration  # noqa: F401
from caracara_archicad_mcp.context import job_store_instance, multi_conn_instance
from caracara_archicad_mcp.jobs import JobSettings, JobStore


@pytest.fixture(autouse=True)
def job_store(monkeypatch, job_clock):
    monkeypatch.setattr("caracara_archicad_mcp.app.job_settings", JobSettings())
    store = JobStore(clock=lambda: job_clock.now)
    job_store_instance.set(store)
    yield store
    store.shutdown()
    job_store_instance.clear()


@pytest.fixture
def job_clock():
    return SimpleNamespace(now=1000.0)


class FakeArchicad:
    """
    In-memory stand-in for a running Archicad instance with the Tapir
    Add-On. Generated tools talk to it through the same
    'core.post_tapir_command' interface as a live connection, so they can
    be tested end-to-end (dispatch, Tapir call, result validation) on
    machines without Archicad.
    """

    def __init__(self, port: int = 19723):
        self.port = port
        self.calls: list[tuple[str, dict | None]] = []
        self._canned_responses: dict[str, dict] = {}
        # Official API commands ('API.GetAllClassificationSystems') share the canned responses by full name.
        self.core = SimpleNamespace(post_tapir_command=self._post_tapir_command, post_command=self._post_tapir_command)
        self.product_info = ProductInfo(version=28, buildNumber=6003, languageCode="USA")
        self.archicad_location = ArchicadLocation(archicadLocation="/Applications/GRAPHISOFT/Archicad 28/ARCHICAD")
        self.archicad_id = SoloProjectID(projectName="FakeProject", projectPath="/path/to/fake_project.pln")
        # Element sets remember the project they were found in, so every set-making call asks for it.
        self.on_tapir_command(
            "GetProjectInfo",
            {
                "isUntitled": False,
                "isTeamwork": False,
                "projectLocation": "/path/to/fake_project.pln",
                "projectPath": "/path/to/fake_project.pln",
                "projectName": "FakeProject",
            },
        )

    def on_tapir_command(self, command: str, response: dict | Callable[[dict | None], dict]) -> None:
        """Registers the canned response for a Tapir command, or a function of its parameters that builds it."""
        self._canned_responses[command] = response

    def _post_tapir_command(self, command: str, parameters: dict | None = None) -> dict:
        self.calls.append((command, parameters))
        if command not in self._canned_responses:
            raise RuntimeError(
                f"FakeArchicad received unexpected Tapir command '{command}'. "
                f"Register a response with on_tapir_command() first."
            )
        response = self._canned_responses[command]
        return response(parameters) if callable(response) else response


class FakeTapirHttp:
    """
    Simulates the Archicad JSON API at http://127.0.0.1:19723 below the real
    multiconn HTTP client, so request envelopes, Tapir error envelopes and
    transport failures are exercised without a running Archicad.
    """

    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self._responses: dict[str, Any] = {}

    def on_tapir_command(self, command: str, result: Any = None, *, error: dict | None = None) -> None:
        """Answer a Tapir command with a result, a Tapir error, or an exception to raise."""
        self._responses[command] = {"error": error} if error is not None else result

    def handle(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append((str(request.url), payload))
        command = payload["parameters"]["addOnCommandId"]["commandName"]
        response = self._responses.get(command)
        if isinstance(response, Exception):
            raise response
        if response is None:
            return httpx.Response(200, json={"succeeded": False, "error": {"code": 4010, "message": "unknown"}})
        return httpx.Response(200, json={"succeeded": True, "result": {"addOnCommandResponse": response}})


@pytest.fixture
def tapir_http(monkeypatch):
    """Patches multiconn's HTTP client so every request is answered by FakeTapirHttp."""
    from multiconn_archicad.clients.core import core_commands

    fake = FakeTapirHttp()
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(fake.handle), **kwargs)

    monkeypatch.setattr(core_commands.httpx, "Client", client_factory)
    return fake


@pytest.fixture
def fake_archicad():
    """
    Provides a FakeArchicad wired into the multi_conn_instance ContextVar
    and ensures all generated tools are registered for dispatch.
    """
    from caracara_archicad_mcp.tools.registration import register_all_tools

    register_all_tools()

    fake = FakeArchicad()
    headers = {Port(fake.port): fake}
    multi_conn = SimpleNamespace(
        refresh=SimpleNamespace(all_ports=Mock(), from_ports=Mock()),
        connect=SimpleNamespace(all=Mock(), from_ports=Mock()),
        active=headers,
        open_port_headers=headers,
    )

    multi_conn_instance.set(multi_conn)
    yield fake
    multi_conn_instance.clear()
