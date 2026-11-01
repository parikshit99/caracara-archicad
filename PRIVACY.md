# Privacy

Caracara runs on your computer and talks to the Archicad open on it. It has no account, no
telemetry and no analytics, and it sends nothing to its maintainers or to anyone else.

## What reaches your AI provider

Your AI client (Claude, or another MCP client) sends what Caracara's tools return to the AI
provider you use, as part of the conversation. That can include:

- **Model data** the AI asks for: element types, IDs, properties, quantities, layers, stories.
- **Project details.** `archicad_project_overview` returns the project's name and stories.
  Its file path, location and Project Info (client, address, ...) are returned only when the AI
  asks for them with `include_private=true`.
- **Pictures of the design**, only when you turn on "Allow pictures of the design" (off by
  default, or `--allow-images`).

What happens to that data next is set by your AI provider's own terms and privacy policy, not
by Caracara. To limit what is sent:

- Turn on **read-only mode** if the AI only needs to look.
- Leave **pictures** off.
- Ask the AI for counts and totals rather than full element lists; Caracara keeps element lists
  in memory on your computer and gives the AI a short `setId` instead.

## What stays on your computer

- **Element sets and job results** are kept in memory while the server runs: an unused set
  expires after an hour, a finished job after a day, and both are gone when the server stops.
- **Calculations** run in a temporary in-memory database that is discarded after each query.
- **Files Archicad writes** for the AI (exports, screenshots) go into Caracara's output folder,
  by default `caracara-archicad-mcp` in your temporary folder (`--output-root` changes it).
- **A log file** at `~/.tapir_mcp/logs/tapir_mcp_server.log` (on Windows,
  `C:\Users\<you>\.tapir_mcp\logs\tapir_mcp_server.log`)
  records which commands ran, on which Archicad port, and error messages, which can include
  text from Archicad. It is never sent anywhere. Delete it whenever you like.

## Network connections

- The server connects only to Archicad on this computer.
- In HTTP mode it listens on this computer only, unless you pass `--allow-remote` with a
  token.
- The first time it starts, the `uv` tool that launches it downloads a Python and the
  dependencies pinned in `uv.lock`, from GitHub and PyPI. That is `uv`
  installing software, not Caracara sending your data.

## Changes

Changes to this page are made in the open, in the repository's history. Questions go to the
[issue tracker](https://github.com/parikshit99/Caracara-Archicad-MCP/issues).
