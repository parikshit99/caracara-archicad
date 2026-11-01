# Decisions

Why Caracara is built the way it is. Read this before re-opening a decision: if an entry already
covers it, change the decision here first, with the maintainer's agreement, then the code.
Plans and ideas not yet decided are in [ROADMAP.md](../ROADMAP.md).

Each entry says what was decided, why, and where the idea came from: **upstream**
([SzamosiMate/tapir-archicad-MCP](https://github.com/SzamosiMate/tapir-archicad-MCP)),
**alesdev88** ([alesdev88/Archicad-MCP](https://github.com/alesdev88/Archicad-MCP)), the
**live test**, or **Caracara** itself.

## Where other projects fit

- **Upstream is the base.** Caracara is a fork and merges upstream after each upstream release
  (see "Upstream" in [CLAUDE.md](../CLAUDE.md) and R23). Where upstream has a feature Caracara
  also has, upstream's is kept.
- **REVIEW.md wins over upstream.** When an upstream change breaks one of Caracara's rules,
  above all the safety and privacy rules R13–R17, Caracara changes it back as little as
  possible and lists it under [Deliberate differences from upstream](#deliberate-differences-from-upstream).
  Every upstream merge is checked against that list, so a merge does not undo a difference.
- **alesdev88/Archicad-MCP is a source of ideas, never of code.** It is a separate MCP server
  with a different design (many dedicated tools). Caracara does not merge from it. An idea from
  it goes into ROADMAP.md and is built, if at all, under the same rules as any other change.
  Its findings about Archicad's behaviour are claims to check against a live Archicad, not facts.

## Deliberate differences from upstream

Check each of these after every upstream merge.

| Difference | Why | Where |
| :--- | :--- | :--- |
| Package `caracara_archicad_mcp` instead of `tapir_archicad_mcp`; `tapir_archicad_mcp` stays as a compatibility shim. | Caracara's own name, without breaking configurations written for upstream. | `src/tapir_archicad_mcp/__init__.py` |
| Name, version, description and `user_config` in `manifest.json` and `pyproject.toml` are Caracara's; only dependency pins are taken from upstream. | Caracara's bundle and releases are its own. | `manifest.json`, `pyproject.toml` |
| Images from Archicad are sent as image content only when the user allows images; otherwise they are left out with a note (2026-11-01). | Upstream returns them to every caller. Pictures of the design are a new kind of data (R14), and base64 sent as text cost hundreds of thousands of tokens per picture. | `images.py` |
| `CloseProject` and `NewProject` are generated but refused unless the user allows session commands; `QuitArchicad` is left out (2026-11-01). | They discard unsaved work, and Archicad cannot say whether there is any. | `safety.py`, `scripts/generator_config.py` |
| Some Tapir commands are left out because the official API covers them, or their schemas are malformed. | One command per job; broken schemas cannot be validated. | `scripts/generator_config.py` |
| Upstream dropped Tapir `GetLayers`; the project overview reads layers from the official `GetLayerAttributes` (2026-11-01). | Followed upstream instead of keeping a command it removed. | `tools/custom/overview.py` |

## Decisions

### Dependencies

- **Keep `multiconn-archicad`; do not fork it** (2026-10-23, Caracara). It provides the command
  schemas and the connection to Archicad. A fork would be ours to maintain forever. Instead, a
  weekly CI job compares Tapir's commands with the ones Caracara wraps and opens an issue when
  they drift (`scripts/check_tapir_commands.py`).
- **SQLite for calculations, not DuckDB** (2026-10-23, Caracara). SQLite comes with Python, so no
  new dependency (R11). It is locked down: an authorizer allows only reading, `query_only` is on,
  a progress handler stops queries after 5 seconds, and results are capped at 500 rows.

### Safety

- **Read-only mode is an allowlist by command name** (2026-10-23, Caracara). Only `Get…`, `Is…`
  and `Filter…` commands, plus a few reviewed ones, are allowed, so a command added by a future
  Tapir release is blocked until someone reviews it (R15).
- **Window pictures are allowed in read-only mode** (2026-10-24, live test). Saving the window
  writes a file, but only into the output folder, and changes nothing in the project. The
  allowlist names `SaveCurrentWindowAsImage` explicitly.
- **Every change is checked before it is made** (2026-10-24, live test). The live test showed a
  move going ahead on an out-of-date set: Archicad skipped a deleted wall and a locked one, and
  only the result said so. Now missing, locked or unreserved elements hold the change until
  `confirm=true`. `confirm` is the only way through; there is no separate `allow_partial` flag.
- **A delete that also removes doors or windows waits for `confirm=true`** (2026-10-23, Caracara).
  Archicad deletes hosted elements with their host.
- **Elements are never reserved automatically in Teamwork** (2026-10-23, Caracara). Reserving
  changes what the rest of the team can do, so it stays a person's decision.
- **Large changes are never split into batches** (2026-10-24, Caracara, against an outside
  review's advice). Each batch would be its own undo step, and a failure halfway would leave
  the model half changed. Freezes on large models are to be measured first (ROADMAP.md).
- **`archicad_read_tool` next to `archicad_call_tool`** (2026-11-01, idea from alesdev88). Reads
  get a tool marked read-only, so clients can allow them without asking; everything else stays
  with `archicad_call_tool`. Highlighting and window pictures stay there too, because they change
  the view or write a file (R17).

- **`archicad_build` for new elements** (2026-11-01, Caracara). Making elements through
  `archicad_call_tool` had no dry run, no checks before the first element was made, and no set of
  what was made. The build tool checks the whole list against the create commands' schemas and the
  project's stories first, and returns one set of everything made. Archicad makes each create
  command its own Undo step and the API cannot group them, so a build is one Undo step per element
  type; one `archicad_mutate` delete of the set removes it all. Creating adds and destroys nothing,
  so the tool is not marked destructive (R17).

### Privacy and tokens

- **Pictures are off by default** (2026-10-23, Caracara). The screenshot tool is offered only
  when the user allows images, and its description says pictures go to the AI provider and are
  for context, not for measuring (R14).
- **Private project details only on request** (2026-10-23, Caracara). The project overview leaves
  out the file path, location and Project Info unless asked with `include_private=true`.
- **A few general tools instead of one tool per task** (Caracara). Tool definitions are sent in
  every conversation; Caracara has about ten. alesdev88/Archicad-MCP chose the other way, with
  about sixty dedicated tools, and checks each task more closely as a result.
- **Element sets instead of GUID lists** (Caracara). Element lists
  stay in the server behind a `setId`, so large selections do not fill the conversation.
  Sets are snapshots and do not follow later changes in Archicad; results report the set's age
  and how many of its elements no longer exist.

### Not doing

- **Running scripts the AI writes** (2026-11-01, seen in alesdev88). There is no sandbox, so a
  script could do anything the user can.
- **Building a model from photos** (2026-10-24, Caracara). A photo has no scale and hides most of
  a building; the AI works from dimensions, plans or surveys instead. Reopened 2026-11-01 by the
  maintainer: `archicad_build` is the first step, and a guided workflow from pictures and drawings
  is on the roadmap.

### Releases

- **Report the installed version** (2026-11-01, Caracara). The server tells clients its own version,
  and `discovery_list_active_archicads` returns it as `caracaraVersion`.
- **No built-in update check** (2026-11-01, Caracara). It would make every user's computer call
  GitHub (R13). A hand-installed bundle is updated by downloading it again; listing the plugin in
  Anthropic's directory is on the roadmap.
- **A Claude plugin beside the `.mcpb`** (2026-11-01, Caracara). Anthropic's directory no longer
  takes `.mcpb` bundles, only plugins, so the repository is also a plugin: `plugin.json` starts
  the same server with the same settings and adds the `working-in-archicad` skill. The server is
  declared in `plugin.json`, not in a root `.mcp.json`, which Claude Code would also offer as a
  project server to everyone who opens the repository. A plugin's local server runs in Claude
  Code and Cowork but not in ordinary chats, so the `.mcpb` stays for Claude Desktop.
  `scripts/release.py` gives both manifests the package version, and `tests/unit/test_plugin.py`
  keeps the plugin's server, settings and skill in step with the bundle and the tools.
