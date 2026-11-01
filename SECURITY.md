# Security

Caracara lets an AI change Archicad projects that are often under client confidentiality, so
we treat security problems as urgent.

## Report a problem privately

Use GitHub's private reporting: on the repository's **Security** tab, select **Report a
vulnerability**. Please do not open a public issue, and do not attach real project files;
a description and the steps to reproduce on an empty project are enough.

We aim to reply within seven days, and to tell you when a fix is released.

## What counts

Anything that breaks one of Caracara's promises, for example:

- read-only mode allowing a command that changes, deletes, saves or exports anything;
- a file written outside the output folder;
- data sent anywhere other than back to the MCP client;
- the HTTP server reachable from another computer without `--allow-remote` and a token;
- `archicad_mutate` changing or deleting elements without the checks and `confirm=true` it
  documents;
- a dependency with a known vulnerability that affects Caracara.

## Supported versions

Fixes go into `master` and the next release. Only the latest stable release and the weekly
pre-release are supported; update before reporting if you can.
