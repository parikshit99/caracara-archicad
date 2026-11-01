# Roadmap

What we plan to work on, and what we decided against. Nothing here is promised; items move up
or out as the live tests and the community's needs show. Every item still follows
[REVIEW.md](REVIEW.md): a new tool, setting, dependency or network call needs the maintainer's
agreement first. Decisions already made, and why, are in [docs/decisions.md](docs/decisions.md).

## Test live first

These depend on how Archicad really behaves, which CI cannot show. Each starts with a check
against a live Archicad and is built only if the check shows a problem.

- **Read changes back.** Archicad can answer "success" for changes it did not make: elements in
  another window's database, or GDL parameters that the object's Parameter Script overrides.
  `archicad_mutate` trusts Archicad's per-element answers. If a live test shows this happening,
  read the changed fields back after a change and report what did not change as `notApplied`.
- **Property reads that crash Archicad.** [alesdev88/Archicad-MCP](https://github.com/alesdev88/Archicad-MCP) saw Archicad 29 crash on
  `GetPropertyValuesOfElements`, once on a single custom property of a single element (Tapir
  1.5.3). Check on Archicad 30 with Tapir 1.7.3. If it reproduces: a limit on how many elements
  one property read may cover, and a "save first" warning in the tool descriptions.
- **Freezes on large models.** Time a property read over every element and a change to every
  wall. If Archicad stops responding for long, read properties in batches. Changes are never
  split, because that would break the single Ctrl+Z.
- **Teamwork.** Reservation checks are written but not yet tried on a Teamwork project.

## Small improvements

- **Check `classified` queries for 2D elements.** `archicad_query(target="classified")` uses
  Archicad's official `GetElementsByClassification`. Official commands that list elements may
  return model elements only, leaving out labels, dimensions and markers.

## Bigger features to decide on

- **Build from a picture or drawing.** A guided workflow on top of `archicad_build`: the AI
  reads the reference into a table of walls, openings and levels, marks which values it measured
  and which it assumed, gets one known dimension from the user, builds after a dry run, then
  compares a picture of the result with the reference. Plans and dimensioned sketches first;
  photos give a massing model at best.
- **Help installing Tapir.** A command the user runs by hand that puts a pinned Tapir release
  into Archicad's Add-Ons folder, checked against its sha256, without replacing an existing one.
  It downloads from GitHub, so it needs the maintainer's agreement (R13), and it would not be a
  tool the AI can call.
- **Custom properties in `archicad_calculate`.** Fire ratings, cost codes and other
  user-defined properties as table columns. Depends on the property-read check above.
- **Find properties by name.** A fuzzy search over property and attribute definitions, so the
  AI finds "Fire Rating" without listing all 1,600 built-in properties first.
- **Find elements by property value**, in the style of Find & Select ("walls whose fire rating
  is empty"). Archicad cannot filter by property value itself, so this reads values and
  depends on the property-read check.
- **Office rules.** Checks against an office standard written as YAML (required properties,
  classifications, layers), with a pass or fail per rule and the failing elements as a set.
- **Exact shapes for `archicad_spatial_query`.** `inside` uses bounding boxes, so L-shaped and
  diagonal zones can include elements just outside them.
- **Schedules.** Archicad has no schedule API; another server reads and edits Scheme Settings'
  XML export. Only if people ask for it.

## Decided against

- **Running scripts the AI writes.** Without a sandbox a script can do anything the user can,
  which conflicts with Caracara's safety and privacy goals.
- **One dedicated tool per task.** Dozens of tools cost tokens in every conversation; Caracara
  keeps a few general tools and reaches every command through them.
- **Splitting large changes into batches.** Each batch would be its own undo step, and a
  failure halfway would leave the model half changed.
