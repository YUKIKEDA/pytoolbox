# 0017. Connectivity checks (`ping`, `port`) and CSV generation (`csv-gen`)

- Status: Accepted
- Date: 2026-11-22
- Issue: #74

## Context

M5-2 adds connectivity checks (`ping` and `port`) and M4-1 adds `csv-gen`. `docs/design.ja.md` §5.1 already places two separate entries under System Operations / Network ("疎通確認" and "ポート調査"), and §9 states that `ping` sends one echo via array arguments, TCP `port` checks open/closed and latency, and `csv-gen` writes BOM-prefixed UTF-8 CSV from column types (`text`, `number`, `boolean`, `date`) with an optional seed.

§5 and §9 did not specify the exact CLI command split, default timeout, exit code for unreachable targets vs invalid arguments, GUI form structure, or how `csv-gen` connects to `target_lock` and `HistoryStore`.

## Decision

- `ping` and `port` are exposed as two separate CLI commands (`pytoolbox ping` and `pytoolbox port`) and two separate GUI screens matching §5.1's sidebar items. Rejected: one combined subcommand with mode flags, which would hide `port` from `pytoolbox list` and the sidebar tree.
- Both `ping` and `port` default to a 3-second timeout (`--timeout`) and return exit code `0` with a diagnostic result when a host is unreachable or a port is closed. Invalid hosts, ports outside `1` to `65535`, or non-positive/non-finite timeouts raise `InputError` (exit `1`); OS/runner failures raise `OperationError` (exit `2`). Both GUI screens use immediate POST forms (inputs + Start / Enter on the left, result on the right) rather than SSE streams.
- `pytoolbox csv-gen OUTPUT --rows N --col NAME:TYPE [...] [--seed S] [--overwrite] [--dry-run]` writes UTF-8 with BOM, acquires `target_lock` on the resolved output file, and records non-`--dry-run` runs in `HistoryStore`. Its GUI screen takes output path, row count, optional seed, one-per-line `name:type` column specs (pre-filled with sample columns), `Dry-run`, and `Overwrite` on the left, and renders the written or planned path and row count on the right.
- Unit tests inject the runner for `ping` and the socket connector for `port` so tests never touch the network.

## Consequences

`docs/design.ja.md` §5 and §9 carry the screen and specification updates. `docs/conventions.md` (`Tests` and `CLI`) holds the command contracts and testing boundaries. Subsequent `feat` Issues implement M5-2 and M4-1 once their prerequisite PRs merge.
