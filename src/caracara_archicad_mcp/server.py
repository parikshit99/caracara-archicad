import argparse
import contextlib
import ipaddress
import logging
import math
import sys
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

import caracara_archicad_mcp.model_configuration  # noqa: F401
from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.file_paths import set_output_root
from caracara_archicad_mcp.jobs import JobSettings
from caracara_archicad_mcp.logging_config import setup_logging
from caracara_archicad_mcp.middleware import BearerTokenMiddleware
from caracara_archicad_mcp.settings import (
    env,
    env_flag,
    set_images_allowed,
    set_read_only,
    set_session_commands_allowed,
)
from caracara_archicad_mcp.tools.custom.capture import TOOL_NAME as CAPTURE_TOOL

setup_logging()


def _finite_float(value: str, *, allow_zero: bool) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        requirement = "nonnegative" if allow_zero else "positive"
        raise argparse.ArgumentTypeError(f"Expected a finite {requirement} number.")
    return number


def _positive_float(value: str) -> float:
    return _finite_float(value, allow_zero=False)


def _nonnegative_float(value: str) -> float:
    return _finite_float(value, allow_zero=True)


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Expected a positive integer.")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Archicad Caracara MCP server.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
        help="MCP transport to use. Defaults to stdio.",
    )
    parser.add_argument(
        "--host",
        default=env("HOST", "127.0.0.1"),
        help="Host to bind for HTTP-based transports. Must be a loopback address unless --allow-remote is set.",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        default=env_flag("ALLOW_REMOTE"),
        help="Allow binding a non-loopback host. Requires --token.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(env("PORT", "8000")),
        help="Port to bind for HTTP-based transports.",
    )
    parser.add_argument(
        "--streamable-http-path",
        default=env("STREAMABLE_HTTP_PATH", "/mcp"),
        help="HTTP path for the streamable-http transport.",
    )
    parser.add_argument(
        "--mount-path",
        default=env("MOUNT_PATH", None),
        help="Optional mount path for SSE transport.",
    )
    parser.add_argument(
        "--token",
        default=env("TOKEN", None),
        help="Bearer token required for HTTP-based transports. Ignored for stdio.",
    )
    parser.add_argument(
        "--async-threshold-seconds",
        type=_positive_float,
        default=env("ASYNC_THRESHOLD_SECONDS", "45"),
        help="Maximum execution/poll wait before returning a running JobHandle (default: 45 seconds).",
    )
    parser.add_argument(
        "--job-ttl-seconds",
        type=_nonnegative_float,
        default=env("JOB_TTL_SECONDS", "86400"),
        help="Completed-job and page lifetime after completion; 0 disables age expiry (default: 86400).",
    )
    parser.add_argument(
        "--max-completed-jobs",
        type=_positive_int,
        default=env("MAX_COMPLETED_JOBS", "128"),
        help="Maximum retained completed/failed jobs; oldest are evicted with their pages (default: 128).",
    )
    parser.add_argument(
        "--output-root",
        default=env("OUTPUT_ROOT", None),
        help="Directory that files written by Archicad commands are confined to (default: a temp subdirectory).",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=env_flag("READ_ONLY"),
        help="Refuse every command that could change the project, files or Teamwork state; only reading is allowed.",
    )
    parser.add_argument(
        "--allow-images",
        action="store_true",
        default=env_flag("ALLOW_IMAGES"),
        help="Offer archicad_capture_view, which sends pictures of the design to the AI provider (off by default).",
    )
    parser.add_argument(
        "--allow-session-commands",
        action="store_true",
        default=env_flag("ALLOW_SESSION_COMMANDS"),
        help="Allow CloseProject and NewProject, which discard unsaved changes (off by default).",
    )
    return parser.parse_args()


def sse_paths_from_mount_path(mount_path: str | None) -> dict[str, str]:
    """Preserve the legacy SSE prefix in v2's explicit routes."""
    if not mount_path or mount_path == "/":
        return {}

    prefix = "/" + mount_path.strip("/")
    return {
        "sse_path": f"{prefix}/sse",
        "message_path": f"{prefix}/messages/",
    }


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_bind_host(args: argparse.Namespace) -> None:
    """Refuse non-loopback HTTP binds unless explicitly allowed and token-protected."""
    if args.transport == "stdio" or is_loopback_host(args.host):
        return
    if not args.allow_remote:
        raise SystemExit(
            f"Refusing to bind non-loopback host '{args.host}'. Use 127.0.0.1, or pass --allow-remote with --token."
        )
    if not args.token:
        raise SystemExit(f"Binding non-loopback host '{args.host}' requires --token.")
    logging.warning("Serving MCP on non-loopback host %s; every request must carry the bearer token.", args.host)


def configure_images(allowed: bool) -> None:
    """Images are off unless the user allows them; when off, the capture tool is not offered at all."""
    set_images_allowed(allowed)
    if allowed:
        logging.warning("Images allowed: archicad_capture_view sends pictures of the design to the AI provider.")
    else:
        with contextlib.suppress(ToolError):  # already removed by an earlier call
            mcp.remove_tool(CAPTURE_TOOL)


def main():
    args = parse_args()
    check_bind_host(args)
    set_output_root(args.output_root)
    set_read_only(args.read_only)
    set_session_commands_allowed(args.allow_session_commands)
    if args.read_only:
        logging.info("Read-only mode: commands that could change the project are refused.")
    configure_images(args.allow_images)
    import caracara_archicad_mcp.app as app_module

    app_module.job_settings = JobSettings(
        async_threshold_seconds=args.async_threshold_seconds,
        job_ttl_seconds=args.job_ttl_seconds,
        max_completed_jobs=args.max_completed_jobs,
    )
    sys.argv = [sys.argv[0]]

    logging.info(
        "Starting Archicad Tapir MCP Server with transport=%s host=%s port=%s streamable_path=%s mount_path=%s",
        args.transport,
        args.host,
        args.port,
        args.streamable_http_path,
        args.mount_path,
    )

    # MCPServer configures HTTP options on its transport methods.
    http_options: dict[str, Any] = {"host": args.host}
    if args.transport == "sse" and args.mount_path:
        http_options.update(sse_paths_from_mount_path(args.mount_path))
    elif args.transport == "streamable-http" and args.streamable_http_path:
        http_options["streamable_http_path"] = args.streamable_http_path

    if args.token and args.transport != "stdio":
        import uvicorn

        app = mcp.sse_app(**http_options) if args.transport == "sse" else mcp.streamable_http_app(**http_options)
        uvicorn.run(BearerTokenMiddleware(app, args.token), host=args.host, port=args.port)
        return

    if args.token:
        logging.warning("--token is only used for HTTP-based transports and is ignored for stdio.")

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, port=args.port, **http_options)


if __name__ == "__main__":
    main()
