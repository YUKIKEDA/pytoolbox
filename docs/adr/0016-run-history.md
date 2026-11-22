# 0016. Run history: retention, GUI page, and recently used tools

- Status: Accepted
- Date: 2026-11-22
- Issue: #66

## Context

M3-2 adds SQLite run history at `<state>/history.sqlite3` and the `Logs & History` screen alongside the "recently used tools" section on the home page. `docs/design.ja.md` §4.2 and `docs/conventions.md` (Safeguards) fix the schema fields (tool, arguments as JSON, UTC ISO 8601 start and end, success count, failure count, cancelled, and error message) and state that only non-`--dry-run` runs of write/delete tools are recorded.

§5 and §5.2 did not specify the history screen's columns, row cap, filtering, detail expansion, whether a clear action exists, whether recently used tools include read-only tools, whether the CLI gets a history subcommand, or whether the database trims older rows.

## Decision

- The `Logs & History` screen (`GET /history`) shows up to the latest 200 entries, newest first, in a table with local timestamps, tool name, status (succeeded, partial, failed, or cancelled), success/failure counts, and summary, with a filter by tool name (`?tool=<tool>`). Arguments JSON and full error text expand inline inside each row via `<details>`. There is no clear button, because history serves as an audit log of machine changes. Rejected: a clear-all button or delete action, which would discard audit history, and a separate detail route per run where inline `<details>` keeps everything in one hypermedia table.
- The "recently used tools" section on the home page (`GET /`) lists up to 6 distinct tools from the history database in order of most recent run, and is omitted (heading included) when the database has no entries. Rejected: tracking read-only tools in memory for the current server session, which diverges from §5.1 ("together with run history") and disappears on restart.
- History is queried through the Python library API and `GET /history` in the GUI, with no `pytoolbox history` CLI subcommand. §6, §8, and §9 list no CLI history command; one can be added in its own roadmap row later if needed.
- `<state>/history.sqlite3` stores every run without a row cap or automatic pruning; only the GUI view slices to the latest 200 rows. Write/delete tools run infrequently enough that retaining full history is practical and preserves older failure records for diagnosis.

## Consequences

`docs/design.ja.md` §5 and §5.2 carry the screen and route decisions. `docs/conventions.md` (Safeguards and GUI HTTP) holds the retention, route, and display contracts. M3-2's implementation Issue builds the SQLite store, the `Logs & History` page, and the home page section.
