import asyncio
import json
import logging
import time
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ToolAnnotations
from multiconn_archicad import (
    APIResponseError,
    ConnHeader,
    Port,
    ProductInfo,
    SoloProjectID,
    TapirInfo,
    TeamworkProjectID,
)
from multiconn_archicad.errors import AddOnCommandUnavailable, APIErrorBase
from pydantic import ValidationError

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.context import job_store_instance, multi_conn_instance
from caracara_archicad_mcp.file_paths import confine_write_paths
from caracara_archicad_mcp.images import build_result, extract_images, to_tool_result
from caracara_archicad_mcp.jobs import JobStore
from caracara_archicad_mcp.pagination import PaginationRequest, prepare_result
from caracara_archicad_mcp.request_lifecycle import DispatchLifecycle
from caracara_archicad_mcp.safety import ensure_allowed
from caracara_archicad_mcp.tools.custom.models import (
    CommandOverview,
    CommandSchema,
    DiscoveryResult,
    ProjectType,
    ReadyInstance,
    UnavailableInstance,
)
from caracara_archicad_mcp.tools.tool_registry import (
    TOOL_DISCOVERY_CATALOG,
    ToolRegistryEntry,
    get_tool_entry,
    get_tool_input_schema,
)
from caracara_archicad_mcp.validation_errors import format_validation_error

log = logging.getLogger()

# How long call_and_wait keeps waiting for Archicad before giving up.
JOB_WAIT_LIMIT_SECONDS = 600.0


@mcp.tool(
    name="discovery_list_active_archicads",
    title="List Active Archicad Instances",
    description=(
        "Scans for and lists running Archicad instances. "
        "Returns 'active' (ready to receive commands with their target 'port') "
        "and 'unavailable' (instances that are unresponsive or missing the Tapir Add-On)."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def list_active_archicads() -> DiscoveryResult:
    log.info("Executing list_active_archicads tool...")
    try:
        multi_conn = multi_conn_instance.get()
    except LookupError:
        log.error("CRITICAL: multi_conn_instance context variable not set. Lifespan manager may have failed.")
        raise RuntimeError("Server configuration error: could not access MultiConn instance.") from None

    multi_conn.refresh.all_ports()
    multi_conn.connect.all()

    active_instances: list[ReadyInstance] = []
    unavailable_instances: list[UnavailableInstance] = []

    for port, header in multi_conn.open_port_headers.items():
        if not isinstance(header.product_info, ProductInfo):
            unavailable_instances.append(_build_unresponsive_instance(header.product_info))
        elif (
            not isinstance(header.tapir_info, TapirInfo)
            or not header.tapir_info.is_installed
            or not header.tapir_info.version
        ):
            unavailable_instances.append(_build_missing_tapir_instance())
        else:
            active_instances.append(_build_ready_instance(int(port), header))

    log.info(f"Discovery complete: {len(active_instances)} ready, {len(unavailable_instances)} unavailable.")
    return DiscoveryResult(
        activeInstances=active_instances,
        unavailableInstances=unavailable_instances,
    )


def _build_unresponsive_instance(error: APIResponseError) -> UnavailableInstance:
    """Builds diagnostic info for an unresponsive Archicad instance."""
    return UnavailableInstance(
        reason="unresponsive",
        message=(
            f"Archicad API error: '{error.message}'. "
            "If the error message is actionable, please prompt the user to resolve it."
        ),
    )


def _build_missing_tapir_instance() -> UnavailableInstance:
    """Builds diagnostic info and installation instructions when Tapir is not installed."""
    return UnavailableInstance(
        reason="missing_tapir",
        message=(
            "Archicad was detected, but the Tapir Add-On is not installed. "
            "The Tapir Add-On is required for AI automation. "
            "Please prompt the user to download and install it from: "
            "https://github.com/ENZYME-APD/tapir-archicad-automation"
        ),
    )


def ready_instance(port: int) -> ReadyInstance:
    """Describe the active Archicad instance on a port: project, versions and path."""
    return _build_ready_instance(port, _get_header(port))


def _build_ready_instance(port: int, header: ConnHeader) -> ReadyInstance:
    """Builds a ready-to-use Archicad instance metadata model."""
    project_id = header.archicad_id
    project_type: ProjectType = "untitled"
    project_path: str | None = None

    if isinstance(project_id, TeamworkProjectID):
        project_type = "teamwork"
        project_path = f"teamwork://{project_id.serverAddress}/{project_id.projectPath}"
    elif isinstance(project_id, SoloProjectID):
        project_type = "solo"
        project_path = project_id.projectPath

    tapir: TapirInfo = header.tapir_info

    return ReadyInstance(
        port=port,
        projectName=getattr(project_id, "projectName", "Untitled"),
        projectType=project_type,
        archicadVersion=str(header.product_info.version),
        tapirVersion=tapir.version,
        projectPath=project_path,
        warning=_get_tapir_version_warning(tapir),
    )


def _get_tapir_version_warning(tapir: TapirInfo) -> str | None:
    """Generates a diagnostic warning if the installed Tapir version differs from supported."""
    if tapir.is_older:
        return (
            f"Installed Tapir version ({tapir.version}) is older than supported ({tapir.requiredVersion}). "
            "Most commands will work, but newer commands or parameters may fail. "
            "If you encounter command errors, prompt the user to upgrade Tapir."
        )
    if tapir.is_newer:
        return (
            f"Installed Tapir version ({tapir.version}) is newer than supported ({tapir.requiredVersion}). "
            "Operations should proceed normally."
        )
    return None


@mcp.tool(
    name="archicad_list_commands",
    title="List Available Archicad Commands [DEPRECATED]",
    description=(
        "Lists every Archicad command name with a short description. Prefer archicad_query/archicad_mutate "
        "when they cover the task; otherwise pick a name here and read its schema with "
        "archicad_get_command_schema before calling it."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def archicad_list_commands() -> list[CommandOverview]:
    log.info("Executing archicad_list_commands tool...")
    return [
        CommandOverview(
            name=tool["name"],
            description=tool["description"],
        )
        for tool in TOOL_DISCOVERY_CATALOG.values()
    ]


@mcp.tool(
    name="archicad_get_command_schema",
    title="Get Archicad Command Schema [DEPRECATED]",
    description=(
        "Returns the JSON schema of one command's 'arguments', for a name from archicad_list_commands. "
        "Call it before archicad_call_tool rather than guessing parameters."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def archicad_get_command_schema(command_name: str) -> CommandSchema:
    log.info(f"Executing archicad_get_command_schema for: {command_name}")
    try:
        tool = TOOL_DISCOVERY_CATALOG[command_name]
    except KeyError:
        raise ToolError(
            f"Command '{command_name}' not found. Please use 'archicad_list_commands' "
            f"to verify the exact spelling of the command name."
        ) from None

    return CommandSchema(
        name=tool["name"],
        input_schema=get_tool_input_schema(command_name),
    )


@mcp.tool(
    name="archicad_call_tool",
    title="Execute Archicad API Command [DEPRECATED]",
    description=(
        "Executes an Archicad command by 'name' with 'arguments' matching its schema, including the 'port' "
        "from discovery_list_active_archicads. For a 'next_page_token', repeat the call with 'page_token' added "
        "to 'arguments'. Long runs return status='running' and a jobHandle for archicad_get_job; after a lost "
        "response, list jobs with archicad_get_job() before retrying a write. Files Archicad writes are confined "
        "to the server's output directory; relative paths are placed there."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def archicad_call_tool(name: str, arguments: dict) -> Annotated[CallToolResult, dict]:
    log.info(f"Executing archicad_call_tool for tool: {name}")

    if "targetSetId" in arguments and arguments["targetSetId"] is not None:
        arguments = dict(arguments)
        target_set_id = arguments.pop("targetSetId")
        if "params" in arguments and isinstance(arguments["params"], dict):
            arguments["params"] = dict(arguments["params"])
            arguments["params"].setdefault("targetSetId", target_set_id)
        elif "params" not in arguments:
            arguments["params"] = {"targetSetId": target_set_id}

    if "fields" in arguments and arguments["fields"] is not None:
        arguments = dict(arguments)
        fields_arg = arguments.pop("fields")
        if "params" in arguments and isinstance(arguments["params"], dict):
            arguments["params"] = dict(arguments["params"])
            arguments["params"].setdefault("fields", fields_arg)
        elif "params" not in arguments:
            arguments["params"] = {"fields": fields_arg}

    tool_entry = get_tool_entry(name)
    ensure_allowed(TOOL_DISCOVERY_CATALOG[name]["title"])
    try:
        validated_arguments = tool_entry.arguments_model.model_validate(arguments)
    except ValidationError as e:
        message = format_validation_error(name, e)
        log.error(message)
        raise ToolError(message) from e
    if tool_entry.params_model:
        validated_arguments.params = confine_write_paths(validated_arguments.params)

    store = job_store_instance.get()
    port = validated_arguments.port
    if tool_entry.pagination_field is not None and validated_arguments.page_token is not None:
        # Continuations read retained data and do not create execution jobs.
        return build_result(await asyncio.to_thread(_read_cached_page, name, tool_entry, validated_arguments, store))
    job = store.submit(
        name,
        port,
        arguments,
        lambda handle: _execute_tool(name, tool_entry, validated_arguments, handle),
    )
    await store.wait(job)
    return to_tool_result(tool_entry, store.initial_result(job))


def _read_cached_page(name: str, tool_entry: ToolRegistryEntry, arguments: Any, store: JobStore) -> dict:
    # MultiConn's public header properties can trigger discovery; keep that work
    # off the event loop, and never redispatch the original command.
    multi_conn = multi_conn_instance.get()
    connection = multi_conn.open_port_headers.get(Port(arguments.port))
    request = _pagination_request(name, tool_entry, arguments, connection)
    return store.get_page(arguments.page_token, request)


def _execute_tool(name: str, tool_entry: ToolRegistryEntry, validated_arguments: Any, job_handle: str) -> Any:
    port = validated_arguments.port
    conn_header = _get_header(port)

    call_args: dict[str, Any] = {"conn_header": conn_header}

    if tool_entry.params_model:
        call_args["params"] = validated_arguments.params

    with DispatchLifecycle(name, port, job_handle):
        result = _dispatch_command(name, tool_entry, call_args, port)
    if tool_entry.pagination_field is None:
        return result
    request = _pagination_request(name, tool_entry, validated_arguments, conn_header)
    return prepare_result(result, request, job_handle.removeprefix("job:"))


def _dispatch_command(name: str, tool_entry: ToolRegistryEntry, call_args: dict, port: int) -> Any:
    try:
        return tool_entry.callable(**call_args)
    except AddOnCommandUnavailable as e:
        raise ToolError(
            f"Command '{name}' is not available in the installed Tapir Add-On on port {port}. "
            f"This indicates that the Tapir Add-On is outdated. "
            f"Please prompt the user to upgrade Tapir from https://github.com/ENZYME-APD/tapir-archicad-automation. "
            f"(Details: {e.message})"
        ) from e
    except APIErrorBase as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        log.error(f"Error executing dispatched tool {name}: {e}")
        raise


def _pagination_request(
    name: str,
    tool_entry: ToolRegistryEntry,
    arguments: Any,
    connection: ConnHeader | None,
) -> PaginationRequest:
    return PaginationRequest(
        connection=connection,
        port=arguments.port,
        command=name,
        parameters=_serialized_parameters(arguments.params if tool_entry.params_model else None),
        field=tool_entry.pagination_field,
    )


def _serialized_parameters(params: Any) -> str:
    parameters = params.model_dump(mode="json", by_alias=True, exclude_none=True) if params is not None else {}
    return json.dumps(parameters, sort_keys=True, separators=(",", ":"))


def _get_header(port: int) -> ConnHeader:
    multi_conn = multi_conn_instance.get()
    if Port(port) not in multi_conn.active:
        # A fresh server knows no ports until discovery runs, and the model may already know
        # the port from an earlier conversation; look at that one port instead of failing.
        multi_conn.refresh.from_ports(Port(port))
        multi_conn.connect.from_ports(Port(port))
    try:
        return multi_conn.active[Port(port)]
    except KeyError:
        raise ToolError(f"Port {port} is not an active Archicad connection.") from None


@mcp.tool(
    name="archicad_get_job",
    title="Get or List Archicad Jobs",
    description=(
        "With no job_handle, immediately lists all retained executions newest first, including fast, completed, "
        "and failed jobs, with command, port, timestamps, and a short argumentsPreview. "
        "Use this to recover after a lost response before retrying a write; similar calls may be ambiguous. "
        "With a job_handle, waits up to the configured threshold and returns running, completed with result, "
        "or failed with error. Polling never redispatches commands."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def archicad_get_job(job_handle: str | None = None) -> Annotated[CallToolResult, dict]:
    store = job_store_instance.get()
    if job_handle is None:
        return build_result(store.list_jobs())
    job = store.get(job_handle)
    await store.wait(job)
    response = store.response(job)
    stripped, images = extract_images(get_tool_entry(job.command), response.get("result", {}))
    if images:
        response = {**response, "result": stripped}
    return build_result(response, images)


def is_running(result: Any) -> bool:
    """True when archicad_call_tool returned a job handle because Archicad is still working."""
    return isinstance(result, dict) and result.get("status") == "running" and "jobHandle" in result


async def call_tool_payload(name: str, arguments: dict) -> Any:
    """archicad_call_tool for callers that work with the result as data, not as an MCP response."""
    return (await archicad_call_tool(name, arguments)).structured_content


async def call_and_wait_result(
    name: str, arguments: dict, limit_seconds: float = JOB_WAIT_LIMIT_SECONDS
) -> CallToolResult:
    """archicad_call_tool for callers that need the finished response, images included, waiting while Archicad works."""
    result = await archicad_call_tool(name, arguments)
    deadline = time.monotonic() + limit_seconds
    while is_running(result.structured_content):
        if time.monotonic() > deadline:
            raise ToolError("Archicad is still working on it; try again with fewer elements.")
        job = await archicad_get_job(result.structured_content["jobHandle"])
        response = job.structured_content
        if response["status"] == "failed":
            raise ToolError(f"Archicad could not answer: {response.get('error')}")
        if response["status"] == "completed":
            result = build_result(response["result"], job.content[1:])
    return result


async def call_and_wait(name: str, arguments: dict, limit_seconds: float = JOB_WAIT_LIMIT_SECONDS) -> Any:
    """archicad_call_tool for callers that need the answer itself, waiting while Archicad works."""
    return (await call_and_wait_result(name, arguments, limit_seconds)).structured_content


async def call_and_collect(name: str, arguments: dict, field: str) -> list[Any]:
    """Every item of a listing command's result field, across all pages, in order."""
    result = await call_and_wait(name, arguments)
    if not isinstance(result, dict) or not isinstance(result.get(field), list):
        raise ToolError(f"Archicad returned no '{field}': {result}")
    items = list(result[field])
    token = result.get("next_page_token")
    while token:
        page = await call_tool_payload(name, {**arguments, "page_token": token})
        items.extend(page.get(field, []))
        token = page.get("next_page_token")
    return items
