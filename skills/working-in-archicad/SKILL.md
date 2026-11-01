---
name: working-in-archicad
description: Use when working with a running Archicad project through Caracara's tools (archicad_query, archicad_mutate, archicad_calculate, archicad_call_tool and the rest), to read elements and quantities, change or delete elements, or run other Archicad commands. Covers the order of calls, element sets, dry runs before changes, and keeping the user's project data private.
---

# Working in Archicad with Caracara

Caracara connects you to an Archicad that is open on the user's computer. The user's models
are often under client confidentiality, and a wrong change costs them real work, so read only
what the task needs and change nothing they have not agreed to.

## When the tools are missing

The tools come from a server that runs on the user's computer. Claude Code and Cowork start it
from this plugin; ordinary claude.ai and Claude Desktop chats do not run a plugin's local
server. If no `archicad_` tools are available, say so, and point the user to the `.mcpb` bundle
for Claude Desktop (https://github.com/parikshit99/Caracara-Archicad-MCP#installation--setup).
Do not describe the model as if you had read it.

## Start every task the same way

1. `discovery_list_active_archicads` gives the `port` of each running Archicad. If there is more
   than one, ask which project to use. An instance under `unavailable` usually lacks the Tapir
   add-on; tell the user rather than working around it.
2. `archicad_project_overview` once per project: stories, units, layers (and which are locked or
   hidden) and classification systems. Leave `include_private` off unless the task needs the
   file path, location or client details.

## Find and read

- `archicad_query` returns a `setId` instead of a list of GUIDs. Pass that `setId` to the other
  tools. Ask for `output` as a short list of fields when you need values, and read a sample
  before reading everything.
- Count first, then summarise. Do not paste long element lists back to the user.
- For totals (volumes, areas, lengths, materials) use `archicad_calculate` with one SQL query.
  Call it without `sql` to see the columns. Never add numbers up yourself.
- For "near", "inside" and "colliding" questions use `archicad_spatial_query`.
- Sets are snapshots. When a result shows `missingCount` above zero or the model may have
  changed, query again.

## Change

- Run `archicad_mutate` with `dry_run=true` first. Show the user what it reports: elements that
  no longer exist, locked or not reserved elements, and doors or windows a delete would take
  with it. Repeat with `confirm=true` only when the user agrees.
- Each `archicad_mutate` call is one Undo step in Archicad; tell the user that one Ctrl+Z
  undoes it.
- For anything else, find the command with `archicad_list_commands`, read its arguments with
  `archicad_get_command_schema`, and do not guess fields. Send commands that only read (`Get…`,
  `Is…`, `Filter…`) with `archicad_read_tool`, and the rest with `archicad_call_tool`.
- A long command returns a `jobHandle`; follow it with `archicad_get_job`. After a lost
  response, call `archicad_get_job` with no handle to see whether the command ran before you
  send a change again. Never send the same change twice to be safe.
- Files Archicad writes go into Caracara's output folder. Tell the user where a file went.
- If a command is refused because read-only mode is on, or a setting keeps a command off,
  tell the user. Do not look for another command that does the same thing.

## Pictures

`archicad_capture_view` exists only when the user has allowed pictures in the settings, and
each picture is sent to the AI provider. Use pictures to check your work, not to read sizes or
positions: read those with `archicad_query` or `archicad_calculate`.

## Keep project data private

- Do not copy project names, client details, addresses, file paths or property values into web
  searches, other tools or files unless the user asks for that.
- Ask before sending or receiving Teamwork changes, saving, or exporting.
