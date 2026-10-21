# 0008. One-level CLI, safeguards, and build order

- Status: Accepted
- Date: 2026-10-22
- Issue: #11

## Context

The owner's draft has two systems, Developer Tools and System Tools, and shows CLI forms per system (`run`, `util`, `run native:`). It shows a Subprocess runner, SSE, a lock, and a history database for System Tools, and a four-phase roadmap that includes script auto-detection and a background resident at login. A Grill decided each point from the product's requirements.

## Decision

- The CLI is one level: `pytoolbox <tool>`. Tool names are unique, so a caller does not have to remember a system to call a tool. `pytoolbox list` shows each tool's system and category.
- The two systems stay as the execution model and the GUI classification. A tool that changes the machine needs safeguards; a conversion does not.
- Categories follow what the user works on: text and code under Developer Utilities; files, storage, and network under System Operations. Safeguards are per tool, not per category.
- `--dry-run`, a lock per write target, and a history entry belong to tools that write or delete. A second run on a held target raises `OperationError`, because the CLI and the GUI, or a double click, can run the same target at once. A `--dry-run` run is not recorded, because the history says what was done to the machine.
- Only batch `Progress` and UDP `LogEvent` stream: SSE in the GUI, stdout in the CLI. They are the long runs a user watches and cancels.
- Subprocess runs external commands and shell scripts only. Python tools run in process and stop through `cancel`.
- A shell script is a tool's implementation. Python wraps it so the caller gets the same arguments, validation, dry-run, lock, and history as any other tool. A command or screen that runs a script by name or path would skip that wrapper, so there is none, and there is no script auto-detection.
- There is no background resident at login. The owner does not need it.
- Order: foundation; read-only tools; safeguards; tools that write or delete; the subprocess runner and the connectivity check; JSON format, Base64, and UUID. Each tool row ships its CLI and its GUI together. Safeguards come before the tools that need them. The connectivity check calls `ping` through the runner.

## Consequences

`docs/design.ja.md` §2, §4.2, §5.1, §6, and §8 carry these decisions. `docs/conventions.md` holds the contracts. The out-of-scope items are listed in `docs/roadmap.md`.
