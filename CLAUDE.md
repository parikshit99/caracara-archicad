# Working on Caracara

Caracara is an MCP server that lets AI agents work with a running Archicad through the Tapir
add-on and Archicad's JSON API. It is a community project: many people with different
workflows and setups depend on it, and their models are often under client confidentiality.
Correctness, safety and privacy come before features.

Every change must follow the rules in [REVIEW.md](REVIEW.md). They are imported here:

@REVIEW.md

## Layout

| Path | What lives there |
| :--- | :--- |
| `src/caracara_archicad_mcp/server.py` | Command-line options and environment variables, startup. |
| `src/caracara_archicad_mcp/app.py`, `context.py` | The MCP server instance and its shared state. |
| `tools/generated/` | One wrapper per Archicad command, written by `scripts/generate_tools.py`. **Never edit by hand.** |
| `tools/overrides.py` | Hand-written behaviour layered over generated commands. |
| `tools/custom/functions.py` | `archicad_call_tool` and discovery. Every call to Archicad goes through `archicad_call_tool`. |
| `tools/custom/meta_verbs.py` | `archicad_query` and `archicad_mutate`. |
| `tools/custom/models.py` | Pydantic models of the custom tools. |
| `result_registry.py` | Element sets (`setId`) kept in memory instead of sending GUID lists to the model. |
| `field_masking.py`, `pagination.py`, `jobs.py` | Smaller responses, paging, long-running commands. |
| `file_paths.py` | Confines every file Archicad writes to one output directory. |
| `tests/unit`, `tests/integration` | Tests. `tests/conftest.py` has `FakeArchicad`, an in-memory Archicad. |
| `scripts/` | Code generation, release and maintenance scripts. |

## Commands

Run these before every commit; CI runs the same:

```bash
uv sync --locked
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
uv run --locked pytest tests --cov --cov-fail-under=85
```

If you change `manifest.json`: `npx --yes @anthropic-ai/mcpb@2.1.2 validate manifest.json`.
If you change `.claude-plugin/plugin.json` or `skills/`: `claude plugin validate .` (the warning
about this CLAUDE.md is expected: it is for contributors, not for the plugin's users).
After changing the `multiconn-archicad` pin: `uv run python scripts/generate_tools.py`.

## Upstream

Caracara is a fork of [SzamosiMate/tapir-archicad-MCP](https://github.com/SzamosiMate/tapir-archicad-MCP),
with its package `tapir_archicad_mcp` renamed to `caracara_archicad_mcp`. We take upstream's
changes by merging them, so every edit to a file that came from upstream is a future merge
conflict (R23).

- Put new behaviour in new modules (`meta_verbs.py`, `result_registry.py`, `tools/overrides.py`).
  In files that came from upstream, change only the lines needed to hook it in.
- Never reformat, rename or tidy code in files that came from upstream.
- When upstream adds something Caracara also has, keep upstream's and delete ours.
- Merge upstream after each upstream release, while the gap is small:

  ```bash
  git remote add upstream https://github.com/SzamosiMate/tapir-archicad-MCP.git  # once
  git fetch upstream
  git merge upstream/master
  ```

  Git does not always follow the package rename. Move any file the merge adds under
  `src/tapir_archicad_mcp/` or `tests/` with `tapir_archicad_mcp` imports into the Caracara
  package and rename its imports; `src/tapir_archicad_mcp/__init__.py` is our compatibility
  shim and stays. Then run the checks above, and check the deliberate differences listed in
  [docs/decisions.md](docs/decisions.md#deliberate-differences-from-upstream).

## Decisions and other projects

[docs/decisions.md](docs/decisions.md) records why Caracara is built the way it is. Read it before
re-opening a decision, and add an entry when you make one.

- **REVIEW.md wins over upstream.** When an upstream change breaks a Caracara rule (above all
  R13–R17), change it back as little as possible and add it to "Deliberate differences from
  upstream" in docs/decisions.md. After every upstream merge, check each listed difference.
- **[alesdev88/Archicad-MCP](https://github.com/alesdev88/Archicad-MCP) is a source of ideas,
  never of code.** Do not merge or copy from it. An idea from it goes into
  [ROADMAP.md](ROADMAP.md) first, and its claims about Archicad's behaviour are checked live.

## How to work

1. **Understand before changing.** Read the module you are changing, its tests, and the
   code that calls it. Search for an existing helper before writing one.
2. **Agree on the plan for anything non-trivial.** A new tool, a new setting, a new
   dependency or a change to how data leaves the user's machine needs the maintainer's
   go-ahead first. Say what you intend and why, then wait.
3. **Make the smallest change that does the job.** No unrequested features, options,
   refactors or "improvements" alongside it.
4. **Test it.** Write the test that proves the change, see it fail without the change,
   then run the full checks above. Nothing is "done" while a check is red.
5. **Re-read your own diff** as a reviewer would, against REVIEW.md, and fix what you find.
6. **Report honestly.** Say what you verified, what you could not (there is no Archicad in
   CI, so live behaviour is unverified until the maintainer tests it), and any shortcut you
   took.

## Never

- Merge a pull request, push to `master`, rewrite published history or force-push.
- Edit `tools/generated/`, weaken or delete a test, or skip a check to get green.
- Add a dependency, turn on a data-sending feature by default, or widen what read-only mode
  allows, without the maintainer's explicit agreement.
- Put secrets, tokens, real project data or personal data in code, tests, logs or commits.
