# Caracara Archicad MCP Server

A personal, experimental fork of [tapir-archicad-MCP](https://github.com/SzamosiMate/tapir-archicad-MCP) for custom Archicad automation workflows.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/parikshit99/Caracara-Archicad-MCP/actions/workflows/test.yml/badge.svg?branch=master)](https://github.com/parikshit99/Caracara-Archicad-MCP/actions/workflows/test.yml)
[![Latest release](https://img.shields.io/github/v/release/parikshit99/Caracara-Archicad-MCP)](https://github.com/parikshit99/Caracara-Archicad-MCP/releases/latest)

**Website:** https://parikshit99.github.io/Caracara-Archicad-MCP/

> **Note:** This is a downstream, fast-moving fork of [SzamosiMate/tapir-archicad-MCP](https://github.com/SzamosiMate/tapir-archicad-MCP) maintained for custom workflows and experimental Archicad tool bindings. All core credit belongs to Máté Szamosi. If you need the stable upstream release, visit the original repository.

The server is a bridge that lets AI agents and applications (Claude Desktop, Claude Code, Gemini CLI and other MCP clients) work with running Archicad instances. It wraps both the community **Tapir API** and the **official Archicad JSON API**: **458 commands** (404 Tapir + 54 official), generated from the schemas published by [`multiconn_archicad`](https://github.com/SzamosiMate/multiconn_archicad). A few Tapir commands are left out on purpose, listed with reasons in `scripts/generator_config.py`: `QuitArchicad`, which can lose unsaved work (`CloseProject` and `NewProject` are available but refused unless you allow them, see `--allow-session-commands`), ones the official API already covers (for example `DeleteAttributes`, `GetLayers`, `GetLines`, `GetPenTables`), and two revision commands whose published schemas are malformed.

---

## What This Fork Adds

-   **High-level query and mutate tools.** `archicad_query` and `archicad_mutate` cover the most common element work in one call each, so the agent does not have to find, read and call individual commands.
-   **Element sets instead of GUID lists (`result_registry`).** Queries return an opaque `setId` and a count. The element GUIDs stay in server memory, so large selections do not fill the model's context. Sets are bounded (50 sets, 500,000 elements in total, evicted least recently used) and expire after an hour unused.
-   **Field masking (`field_masking`).** Element detail reads return only the fields you ask for (e.g. `["id", "storyIndex", "details.thickness"]`), dropping polygon arrays and other bulky data. Names are matched exactly (dot paths reach nested values, and `storyIndex`/`floorIndex` are interchangeable); names that no element has are listed in `unknownFields`.
-   **Exact calculations (`archicad_calculate`).** Quantity totals, schedules and bills of quantities are computed with one read-only SQL query instead of by the model adding numbers up. The data is loaded into a temporary in-memory SQLite database that cannot write, read files or reach the network, and is discarded after each query; only the query's result is returned to the model.
-   **Pagination.** Large listings are returned 100 items at a time with a `next_page_token`; `archicad_query` follows the pages itself.
-   **Long-running jobs.** Slow commands return a job handle instead of timing out (see [Long-Running Commands](#long-running-commands)).
-   **Safer file output.** Paths that Archicad writes to (exports, archives, Save As, screenshots, publishing) are confined to one output directory.
-   **Safety checks before changes.** Before `archicad_mutate` changes anything, it looks for elements Archicad would skip: elements deleted since the set was found, and elements on a locked layer or not reserved by you in Teamwork. If there are any, nothing is changed and they are listed with their Element IDs until the AI repeats the call with `confirm=true`, so it has to ask first. A delete that would also remove doors, windows, skylights or openings of other elements waits for `confirm=true` the same way. Results give the set's age and how many of its elements no longer exist, because a set does not follow later changes in Archicad. `dry_run=true` returns the same report without changing anything.
-   **Read-only mode.** With `--read-only` (or the "Read-only mode" switch in the `.mcpb` settings), the AI can look at projects but every command that could change, delete, save or export anything is refused. Commands are allowed by name (`Get…`, `Is…`, `Filter…`, plus highlighting, view refreshes and saving a picture of the window into the output folder for screenshots), so a command added by a future Tapir release stays blocked until it is reviewed.
-   **Locked-down HTTP transports.** HTTP binds to loopback by default; binding any other address needs `--allow-remote` and a bearer token.

---

## Tools

| Tool | Purpose |
| :--- | :--- |
| `discovery_list_active_archicads` | Lists running Archicad instances with their `port`, and instances that are unresponsive or missing the Tapir add-on. Reports the installed Caracara version and warns when the installed Tapir version differs from the supported one. |
| `archicad_query` | Finds elements and reads their metadata. `target` is an element type (`Wall`, `Slab`, `Door`, ...) or `all`, `selected`, `classified`, `filter`. `output` is `set` (default: `setId` and count), `ids` (up to 1,000 element identifiers) or a list of field names. |
| `archicad_mutate` | Changes the elements of a `setId` (or an explicit element list), as one Undo step per call. Actions: `set_property`, `move`, `rotate`, `set_details`, `highlight`, `delete`. Elements that no longer exist or cannot be changed, and a delete that would also remove hosted doors or windows, hold the change until `confirm=true`; elements Archicad still skips are reported in `failedElements`, with their Element IDs. Results give the set's age (`setAgeSeconds`) and `missingCount`. `dry_run=true` previews the change without making it. |
| `archicad_build` | Creates walls, columns, beams, slabs, roofs, zones, doors and windows from one list. Each item has a `type`, the fields of that type's create command, an optional `ref`, and for doors and windows a `hostRef` naming a wall of the same build. The whole list is checked against the commands' schemas and the project's stories before anything is made; `dry_run=true` returns that check with the build's plan size (`extent`), so a wrong scale shows before building. Everything made comes back as one `setId`, which one `archicad_mutate` delete removes; in Archicad each element type is its own Undo step. |
| `archicad_calculate` | Runs one read-only SQL `SELECT` over a `setId` (or element list). Table `elements` has one row per element (`guid`, `element_id`, `type`, `story_index`, `story`, `layer` and Archicad's quantities such as `volume`, `gross_volume`, `length`, `area`); table `components` has one row per building-material skin (`material`, `volume`, `projected_area`, `is_core`, `is_finish`, plus the element's type, story and layer); a column's or beam's materials are counted on its segments when those are in the set too. Units are meters, square meters and cubic meters. Without `sql` it lists the available columns. |
| `archicad_project_overview` | Returns, in one read-only call, what an agent needs before working: Archicad and Tapir versions, project name and type, stories (index, name, level, height), calculation units, north direction, layers (with which are locked or hidden) and classification systems. The file path, geographic location and Project Info fields (client, address, ...) are only included with `include_private=true`. |
| `archicad_capture_view` | **Off by default; offered only when the user allows images.** Returns a picture so the agent can check its work: the Archicad window currently in front (optionally the entire 2D drawing), or a 3D, 2D or section preview of one element. Pictures of the design go to the AI provider, which is why the user has to switch this on. |
| `archicad_spatial_query` | Relates two element sets in space. `near`: pairs whose 3D bounding boxes are at most `distance` meters apart (e.g. conduits within 0.5 m of walls); `inside`: elements whose bounding-box center lies in an element of the other set (e.g. furniture per zone); `collides`: Archicad's exact geometry check, including MEP clearance zones. Returns counts, the nearest pairs (named by Element ID) and the matching elements as new `setId`s. `near` and `inside` use a spatial index, so thousands of elements take well under a second; bounding boxes approximate diagonal and irregular shapes. |
| `archicad_list_commands` | Lists all 458 command names with short descriptions. |
| `archicad_get_command_schema` | Returns the JSON schema of one command's arguments. |
| `archicad_call_tool` | Runs any command by name, after validating its arguments against the schema. |
| `archicad_read_tool` | Runs a command that only reads (its name starts with `Get`, `Is` or `Filter`), the same way as `archicad_call_tool`, and refuses any other. It is marked read-only, so a client can allow it without asking each time while still asking before `archicad_call_tool`. |
| `archicad_get_job` | Gets a long-running job's result by handle, or lists recent jobs to recover a lost response. |

`archicad_list_commands`, `archicad_get_command_schema`, `archicad_call_tool` and `archicad_read_tool` are the original low-level interface. They are marked deprecated in favour of `archicad_query`/`archicad_mutate`, but remain the way to reach any command those two do not cover. Every tool carries MCP annotations (read-only, destructive, idempotent) so clients can ask for confirmation before changes.

---

## Installation & Setup

### 1. Prerequisites

-   **Archicad & Tapir Add-On.** Archicad must be running; it provides the official JSON API. Install the [Tapir Archicad Add-On](https://github.com/ENZYME-APD/tapir-archicad-automation) for the full command set. Tested against Archicad 30 with Tapir 1.7.3.
-   **`uv`.** The [uv](https://docs.astral.sh/uv/) package manager must be on the PATH your client sees. It installs a suitable Python (3.12–3.14) and the dependencies itself.
-   **An MCP client**, such as [Claude Desktop](https://claude.ai/download), Claude Code, or [Gemini CLI](https://github.com/google-gemini/gemini-cli).

### 2. One-click install (MCP Bundle, recommended)

If your client supports [MCP Bundles](https://github.com/modelcontextprotocol/mcpb), download a `.mcpb` file and open it with the client. Claude Desktop, Claude Code and MCP for Windows install them directly.

-   **Stable:** [caracara-archicad.mcpb](https://github.com/parikshit99/Caracara-Archicad-MCP/releases/latest/download/caracara-archicad.mcpb) always downloads the latest release. Stable releases are published automatically on the first Friday of each month when `master` has changed, versioned by date (e.g. `2026.11.0`).
-   **Weekly pre-release:** [caracara-archicad-weekly.mcpb](https://github.com/parikshit99/Caracara-Archicad-MCP/releases/download/weekly/caracara-archicad-weekly.mcpb) is rebuilt from `master` every Friday when it has changed, for trying unreleased changes ([release page](https://github.com/parikshit99/Caracara-Archicad-MCP/releases/tag/weekly)).

**Updating.** A bundle you installed from a file does not update itself. To update, download the stable link above again and open it with your client, then restart the client. To see which version you have, look at the extension in your client's settings (Claude Desktop: *Settings › Extensions*), or ask the AI which Caracara version is installed. The weekly pre-release reports the version of the last stable release it builds on.

The bundle can modify the open project, export files, and send or receive Teamwork changes, so treat installing it like handing your client your Archicad seat. Work on a copy of a project you care about, and read [Known issues](docs/known-issues.md) first: what has been tested live, and what can go wrong.

<details>
<summary>If the bundle will not start</summary>

-   **"Server disconnected", or the install itself fails.** Uninstall the bundle, restart the client, then install it again. Installing over an existing bundle can leave stale state behind that a straight reinstall does not clear.
-   **Nothing happens for the first half minute.** That is uv building the environment, and antivirus scanning or a slow connection stretches it out. Restart the client once before assuming the bundle is broken.
-   **Claude Desktop logs** are at `%APPDATA%\Claude\logs\mcp-server-Archicad (Caracara).log`. On the Microsoft Store build that path is redirected and the real file is `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\logs\mcp-server-Archicad (Caracara).log`.

</details>

### 3. Manual configuration

Add the server to your client's configuration. For Claude Desktop the file is:

-   **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
-   **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

The package is not published on PyPI, so install it straight from GitHub with `uvx`:

```json
{
  "mcpServers": {
    "ArchicadCaracara": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/parikshit99/Caracara-Archicad-MCP",
        "caracara-server"
      ]
    }
  }
}
```

Append `@v0.7.0` (or another tag) to the URL to pin a release.

**From a local clone** (for development; see [Development](#development)):

```json
{
  "mcpServers": {
    "ArchicadCaracaraLocal": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/Caracara-Archicad-MCP", "caracara-server"]
    }
  }
}
```

[`claude_desktop_config.example.json`](claude_desktop_config.example.json) has a further example. The commands `archicad-server`, `tapir-archicad-mcp` and `caracara-archicad` are aliases of `caracara-server`, and the `tapir_archicad_mcp` module is kept for configurations written for upstream.

---

## Configuration Options

The server is configured with CLI flags or environment variables. Each variable is read as `CARACARA_MCP_<NAME>` first, then the legacy `TAPIR_MCP_<NAME>`. CLI flags override environment variables.

| CLI Flag | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `--transport` | - | `stdio` | Transport protocol: `stdio`, `sse`, or `streamable-http` |
| `--host` | `CARACARA_MCP_HOST` | `127.0.0.1` | Bind address for HTTP transports; must be loopback unless `--allow-remote` is set |
| `--port` | `CARACARA_MCP_PORT` | `8000` | Bind port for HTTP transports |
| `--allow-remote` | `CARACARA_MCP_ALLOW_REMOTE` | off | Permit a non-loopback `--host` (e.g. `0.0.0.0`); requires `--token` |
| `--token` | `CARACARA_MCP_TOKEN` | none | Bearer token required on every HTTP request; ignored for `stdio` |
| `--streamable-http-path` | `CARACARA_MCP_STREAMABLE_HTTP_PATH` | `/mcp` | HTTP path of the `streamable-http` endpoint |
| `--mount-path` | `CARACARA_MCP_MOUNT_PATH` | none | Path prefix for the `sse` endpoints (`<prefix>/sse`, `<prefix>/messages/`) |
| `--output-root` | `CARACARA_MCP_OUTPUT_ROOT` | `<temp>/caracara-archicad-mcp` | Directory that files written by Archicad commands are confined to; relative paths are placed inside it and missing subfolders are created |
| `--async-threshold-seconds` | `CARACARA_MCP_ASYNC_THRESHOLD_SECONDS` | `45` | Seconds to wait before returning a running job handle |
| `--job-ttl-seconds` | `CARACARA_MCP_JOB_TTL_SECONDS` | `86400` | Seconds to keep finished jobs; `0` disables time-based expiry |
| `--max-completed-jobs` | `CARACARA_MCP_MAX_COMPLETED_JOBS` | `128` | Maximum number of finished jobs to keep |
| `--read-only` | `CARACARA_MCP_READ_ONLY` | off | Refuse every command that could change the project, files or Teamwork state; reading, highlighting, window pictures and dry runs still work |
| `--allow-images` | `CARACARA_MCP_ALLOW_IMAGES` | off | Offer `archicad_capture_view`, which uploads pictures of the design to the AI provider. Pictures help the AI check its work but use more tokens and context. When off, the tool is not offered at all, and the preview and view image commands leave their pictures out and say so, instead of sending them |
| `--allow-session-commands` | `CARACARA_MCP_ALLOW_SESSION_COMMANDS` | off | Allow `CloseProject` and `NewProject`. Archicad discards unsaved changes when either runs and cannot report whether there are any, so they are refused unless you switch this on. Read-only mode still blocks them |

Paths Archicad only reads (opening a project, importing BCF) are not confined, so they can reach your existing files.

---

## Long-Running Commands

Commands normally return their result directly. If a command takes longer than 45 seconds, it returns a job handle while execution continues:

```json
{"status": "running", "jobHandle": "job:83af21..."}
```

Call `archicad_get_job(job_handle="job:83af21...")` to check progress and retrieve the result. Repeat while the status is `running`; the final status is `completed` or `failed`. If your client times out sooner, lower `--async-threshold-seconds`.

If a response is lost, call `archicad_get_job()` to list recent jobs before repeating a command that changes the project. Cancelling a request does not stop a command already started, and retrying it may duplicate changes. Polling never re-runs a command.

Jobs remain available across HTTP client reconnects while the server is running, but are lost on server restart. Retention defaults to 24 hours and a maximum of 128 finished jobs.

---

## Usage

1. **Restart your MCP client** to apply configuration changes.
2. Make sure at least one Archicad instance is running.
3. Ask the AI to find it:

   > *"Can you check what Archicad projects I have running?"*

   The AI calls `discovery_list_active_archicads` and reports the active instances and their `port` numbers.

4. State your goal:

   > *"Using port 19723, find all the walls on the ground floor and highlight them in red."*

5. The AI works through the tools:
   - **Common element work:** `archicad_query(target="Wall", output=["id", "storyIndex"])` returns a `setId` and the requested fields, then `archicad_mutate(target_set=<setId>, action="highlight", payload={...})` changes those elements in one Undo step.
   - **Anything else:** `archicad_list_commands` to find a command, `archicad_get_command_schema` to read its parameters, then `archicad_read_tool` (for `Get…`, `Is…` and `Filter…` commands) or `archicad_call_tool` with the command name, `port` and arguments.

`archicad_query` and `archicad_mutate` default to port 19723, Archicad's first API port, when no `port` is given.

---

## How It Works

-   **AI agent (e.g. Claude):** Interprets prompts and chooses tools.
-   **MCP client (e.g. Claude Desktop):** Runs the server process and relays messages.
-   **MCP server (Caracara):** Exposes the tools above. Every command's arguments are validated against Pydantic models before anything is sent to Archicad, and invalid calls get a short summary of what is wrong.
-   **Generated command layer:** One wrapper per Archicad command, generated by `scripts/generate_tools.py` from the pinned `multiconn-archicad` release. Custom behaviour (element sets, field masking, path confinement) lives in `tools/overrides.py`, so regenerating never loses it.
-   **`multiconn_archicad` library:** Finds running Archicad instances and sends commands to them.
-   **Archicad & Tapir add-on:** Execute the commands and return structured data.

There are no machine-learning dependencies: tool discovery is a plain list and schema lookup, not vector search.

---

## Development

```bash
git clone https://github.com/parikshit99/Caracara-Archicad-MCP
cd Caracara-Archicad-MCP
uv sync                                    # Python and locked dependencies
uv run pytest tests                        # unit and integration tests (no Archicad needed)
uv run ruff check src tests scripts        # lint
uv run ruff format src tests scripts       # format
uv run python scripts/generate_tools.py    # regenerate command wrappers after changing the multiconn-archicad pin
```

Do not edit `src/caracara_archicad_mcp/tools/generated/` by hand: CI regenerates it and fails on any difference. Put custom behaviour in `tools/overrides.py` instead.

Every change, by a person or an AI agent, follows the rules in [REVIEW.md](REVIEW.md); [CLAUDE.md](CLAUDE.md) (and [AGENTS.md](AGENTS.md) for other agents) tells AI agents how to work here. CI enforces the rules it can: function complexity, commented-out code, `print` in the package, private imports across modules, and direct Archicad calls outside the command layer.

### Continuous integration and releases

-   **Every push and pull request to `master`** runs lint and format checks, a lock-file check, a check that generated tools match the generator, a `pip-audit` vulnerability audit of the locked dependencies, tests with at least 85% coverage on Windows, macOS and Linux with Python 3.12–3.14, and a package and MCPB bundle build.
-   **Weekly build** (Fridays 03:30 UTC): when `master` has changed, runs the same gates and replaces the rolling [`weekly`](https://github.com/parikshit99/Caracara-Archicad-MCP/releases/tag/weekly) pre-release.
-   **Monthly stable release** (first Friday, 04:30 UTC): when `master` has changed, runs the gates, bumps the date-based version in `pyproject.toml`, `manifest.json` and `uv.lock`, tags it, and publishes a GitHub release with the wheel, sdist and `.mcpb` bundle.
-   **Tapir command check** (Mondays 03:30 UTC): compares Tapir's latest release and `master` branch with the commands this server wraps or leaves out on purpose (`scripts/check_tapir_commands.py`). When Tapir has a command we lack, or drops one we call, it opens an issue and keeps it updated; the issue closes itself once we are back in sync.
-   **Dependabot** opens weekly grouped updates for Python dependencies and GitHub Actions.

---

## Origins & Acknowledgements

**Caracara Archicad** is a fork of [tapir-archicad-MCP](https://github.com/SzamosiMate/tapir-archicad-MCP), created and originally maintained by [Máté Szamosi (@SzamosiMate)](https://github.com/SzamosiMate).

We are deeply grateful to Máté and the [Enzyme APD](https://github.com/ENZYME-APD/tapir-archicad-automation) team for developing the Archicad automation ecosystem and making it open-source.

**Why this fork?**
This fork is maintained purely as a personal sandbox and experimental testbed for custom workflows. It is not intended to compete with or replace the original project. For production use and the official, stable release, please use the upstream [SzamosiMate/tapir-archicad-MCP](https://github.com/SzamosiMate/tapir-archicad-MCP) repository.

*For issues or questions about this fork, please use the [Caracara issue tracker](https://github.com/parikshit99/Caracara-Archicad-MCP/issues) rather than the upstream one.*

The Caracara icon is this project's own artwork, a crested caracara; the full-size version is in [`branding/`](branding/).

---

## Contributing

Issues and pull requests are welcome on the [Caracara repository](https://github.com/parikshit99/Caracara-Archicad-MCP). Please run the checks in [Development](#development) before opening a pull request. [ROADMAP.md](ROADMAP.md) lists what we plan to work on, [docs/decisions.md](docs/decisions.md) why Caracara is built the way it is, and [docs/known-issues.md](docs/known-issues.md) what has been tested live and what is known to be limited.

---

## License

This project is licensed under the MIT License. See the [LICENSE](./LICENSE) file for details.
