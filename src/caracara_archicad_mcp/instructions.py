"""What the server tells every client when it connects: the short form of skills/working-in-archicad.

Clients add this text to the conversation once, so it stays short; the tool descriptions carry
the details of each tool.
"""

SERVER_INSTRUCTIONS = """\
Caracara works with an Archicad open on the user's computer. Models are often under client \
confidentiality, and a wrong change costs the user real work.

- Start with discovery_list_active_archicads for the port (ask which project if there are \
several), then archicad_project_overview once. Leave include_private off unless the task needs \
the path, location or client details.
- Find elements with archicad_query: it returns a setId to pass to the other tools instead of \
GUID lists. Count first, read a few fields of a sample, and summarise rather than list.
- Get totals from archicad_calculate, never by adding numbers up yourself.
- Before any change, run archicad_mutate or archicad_build with dry_run=true and show the user the \
report. Go ahead only when they agree, adding confirm=true when archicad_mutate asks for it. \
Build from a picture or drawing only after the user confirms a known dimension against the dry run's extent.
- For other commands, read the schema with archicad_get_command_schema instead of guessing; use \
archicad_read_tool for commands that only read.
- After a lost response, list jobs with archicad_get_job before sending a change again.
- If read-only mode or a setting refuses a command, tell the user; do not look for another \
command that does the same.
- Do not copy project names, client details, paths or property values into web searches, other \
tools or files unless the user asks.
"""
