# Known issues

What has been tested against a live Archicad, what is known to be limited, and what others have
reported but we have not yet checked. CI has no Archicad, so anything not listed under
"Verified live" is untested in real use.

Please report what you find on the
[issue tracker](https://github.com/parikshit99/Caracara-Archicad-MCP/issues), with your
Archicad, Tapir and Caracara versions (ask the AI which Caracara version is installed).

## Verified live

On **Archicad 30 with Tapir 1.7.3**, 24 October 2026, on a copy of a project with walls, doors
and windows, slabs, zones with furniture, columns and a locked layer:

- **Dry runs** change nothing and list elements that cannot be changed.
- **Deleting a wall that holds a door** waits for `confirm=true`.
- **Every change is one undo step**: one Ctrl+Z undoes it.
- **Changes are checked first.** A move of a set with one deleted wall and one wall on a locked
  layer was held and named the locked wall (`SW - 005`). With `confirm=true` the other four walls
  moved, and both skipped walls were listed in `failedElements`.
- **Read-only mode** refused saving and moving and allowed a window screenshot.

Problems the live test found, all fixed since:

- Calls with a known port failed right after the server started, until
  `discovery_list_active_archicads` had run.
- A material takeoff counted column materials twice, once on the column and once on its
  segments.
- A move went ahead on an out-of-date set; only the result said that elements were skipped.
- Results named elements by GUID only, so the AI needed another lookup to say which wall it meant.

## Not yet verified live

- **Teamwork.** The check for elements not reserved by you is written but has not been tried on
  a Teamwork project.
- **Large models.** How long queries, calculations and changes take on tens of thousands of
  elements, and whether Archicad stops responding meanwhile.
- **Commands outside `archicad_query` and `archicad_mutate`.** Most of the 458 commands are
  generated from their published schemas and validated, but have not each been run live.
- **`archicad_build`.** Written and tested against the create commands' schemas, not yet run on
  a live Archicad: how doors are placed along their wall, and which tool defaults fill the
  fields a build leaves out.
- **Session commands.** `CloseProject` and `NewProject` behind `--allow-session-commands`.
- **The Claude plugin.** It passes `claude plugin validate`, and Claude Code on Linux starts its
  server; it has not been run with Archicad, on Windows, or in Cowork.

## Known limits

These are how Caracara works today, not bugs.

- **Element sets are snapshots.** A `setId` keeps the elements found at query time. Elements
  drawn later are not in it, and deleted ones are reported as missing. Results give the set's
  age (`setAgeSeconds`); query again when it matters. Unused sets expire after an hour.
- **`inside` uses bounding boxes.** `archicad_spatial_query(relation="inside")` can count an
  element just outside an L-shaped or diagonal zone. `collides` uses Archicad's exact check.
- **No custom properties in calculations.** `archicad_calculate` has element details and
  Archicad's quantities, but not user-defined properties such as fire ratings or cost codes.
- **Screenshots are for context.** The AI cannot judge exact sizes or positions from a picture.
  `entire_drawing` makes no difference in a 3D window. With images switched off, pictures are
  left out of results and a note says so.
- **A hand-installed bundle does not update itself.** Download the stable `.mcpb` again to
  update. The weekly pre-release reports the version of the last stable release.

## Reported by others, not yet checked

From [alesdev88/Archicad-MCP](https://github.com/alesdev88/Archicad-MCP/blob/main/docs/known-issues.md),
another Archicad MCP server. These are their findings, on their setup; we list them so they can
be checked.

- **Reading property values can crash Archicad.** `GetPropertyValuesOfElements` crashed
  Archicad 29 (build 4006, Tapir 1.5.3) three times, once on a single user-defined property of
  a single element, losing unsaved work. Caracara's own tools do not read property values, but
  the AI can call the command directly. **Save before asking the AI to read properties** until
  this is checked on your version.
- **Archicad can report success for changes it did not make**: for elements outside the active
  window's database, and for GDL parameters that the object's Parameter Script overrides.
- **Official commands that list elements may leave out 2D elements** (labels, dimensions,
  markers). `archicad_query` uses Tapir's commands for `all`, types and the selection; only
  `classified` uses the official `GetElementsByClassification`.
