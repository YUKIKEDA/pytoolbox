# Conventions

Agent entry: [AGENTS.md](../AGENTS.md). There is no `CONTRIBUTING.md`.

## Layout

uv workspace. The root project is not published (`tool.uv.package = false`).

```text
packages/pytoolbox/     core library and CLI
  src/pytoolbox/cli.py       `pytoolbox` command: list, gui, one subcommand per tool
  src/pytoolbox/errors.py    PytoolboxError, InputError, OperationError
  src/pytoolbox/results.py   Progress, ItemFailure, BatchResult, LogEvent, Cancel
  src/pytoolbox/registry.py  Tool, System, TOOLS, one constant per tool
  src/pytoolbox/worker.py    Worker: a process for a call that may never return
  src/pytoolbox/tools/dev/   Developer Utilities (text.py, regex.py, resx.py, hardcoding.py)
  src/pytoolbox/tools/system/  System Operations (folder_size.py)
packages/pytoolbox-gui/ local web GUI, started by `pytoolbox gui`
  src/pytoolbox_gui/cli.py      serve(): port check, uvicorn, browser
  src/pytoolbox_gui/server.py   create_app(): routes, Host and Origin guard, errors
  src/pytoolbox_gui/sse.py      Run, Runs, SSE encoding
  src/pytoolbox_gui/screens.py  Screen, Fragment, SCREENS, search matching, each tool's form handler
  src/pytoolbox_gui/templates/  shell, pane, and event fragments
  src/pytoolbox_gui/static/     app.css, htmx, the SSE extension, icons/ with its LICENSE
packages/<name>/tests/  tests for that package
docs/design.ja.md       the owner's draft spec, Japanese
docs/roadmap.md         ID, title, Issue, status
docs/adr/               one decision per file, NNNN-slug.md
native-scripts/         created with the Issue that adds a shell script
.dev/                   ignored local notes
```

Do not add an empty module, recipe, or script ahead of the Issue that needs it.

A tool row adds one `Tool` to `registry.TOOLS` and one `Screen` to `screens.SCREENS`, in the same PR. `create_app` refuses a tool without a screen and a screen without a tool, so the CLI and the GUI cannot drift apart. It also refuses a screen whose icon is not under `static/icons/`, so the row copies its icon in the same PR.

A row that adds a module adds its line to this file in the same PR.

## Product

Recorded in [docs/adr/0002-inventory-ui-license.md](adr/0002-inventory-ui-license.md). The tool list and the GUI stack live in `docs/design.ja.md`. The license is MIT (`LICENSE`).

## Toolchain

Recorded in [docs/adr/0001-toolchain.md](adr/0001-toolchain.md).

| Concern         | Choice                                                 |
| --------------- | ------------------------------------------------------ |
| Python          | 3.12 (`requires-python >= 3.12`, `.python-version`)    |
| Env and lock    | uv workspace, `uv.lock` committed                      |
| Format and lint | ruff (`just lint` fails on drift)                      |
| Types           | pyright, `standard`                                    |
| Tests           | pytest, branch coverage at least 90% (`just coverage`) |
| GUI tests       | FastAPI `TestClient`, `httpx2` in the dev group        |
| Tasks           | just                                                   |

## Python

The machine check is `just lint`: `ruff format --check`, `ruff check`, and pyright. `docs/adr/0004-python-style.md` records why this set. A `# noqa` names one code and the reason on that line. Do not disable a code for a whole file to finish a change.

### Format

`ruff format` is the only formatter. Do not hand-wrap a line to fight it.

| Setting           | Value                         |
| ----------------- | ----------------------------- |
| Line length       | 100                           |
| Indent            | 4 spaces                      |
| Quotes            | `"`                           |
| Line endings      | LF                            |
| Docstring samples | formatted with the same rules |

### Lint

Enabled codes are `[tool.ruff.lint] select` in `pyproject.toml`. The groups:

| Group                                                                                                                  | What it keeps out                                                                 |
| ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `E`, `W`, `F`                                                                                                          | pycodestyle and pyflakes                                                          |
| `I`                                                                                                                    | import order: stdlib, third party, first party                                    |
| `N`                                                                                                                    | `snake_case` modules and functions, `PascalCase` classes, `UPPER_SNAKE` constants |
| `UP`                                                                                                                   | syntax older than 3.12 (`Optional`, `List`, `datetime.utcnow`)                    |
| `ANN`                                                                                                                  | a public function without a parameter or return annotation                        |
| `D`                                                                                                                    | a public module, class, or function without a Google docstring                    |
| `B`, `BLE`, `S`, `TRY`                                                                                                 | bare `except`, `shell=True`, swallowed failures                                   |
| `PTH`                                                                                                                  | `os.path` and bare `open` where `pathlib` does the job                            |
| `T20`                                                                                                                  | `print` in library code                                                           |
| `PT`                                                                                                                   | a test that is not a pytest function                                              |
| `ERA`, `TD`                                                                                                            | commented-out code, and a TODO that does not name an issue                        |
| `A`, `C4`, `SIM`, `RET`, `ARG`, `PIE`, `PERF`, `FURB`, `LOG`, `ISC`, `DTZ`, `ASYNC`, `PLC`, `PLE`, `PLW`, `TID`, `RUF` | the smaller bugs and style nits in those plugins                                  |

Left off on purpose:

| Left off      | Why                                                                                       |
| ------------- | ----------------------------------------------------------------------------------------- |
| `PLR`         | Argument counts and complexity limits fight a tool that takes the options the task needs. |
| `FBT`         | A CLI flag is a boolean (`dry_run`).                                                      |
| `TC`          | Imports stay at the top. No `TYPE_CHECKING` shuffle.                                      |
| Preview rules | The gate stays on stable ruff.                                                            |

Tests (`packages/**/tests/**`) skip `D` and `S101`. A test name is the docstring. `assert` is how pytest checks a result.

`print` belongs to the CLI process, not to a library function. When a CLI module is added, its `T20` ignore is a `per-file-ignores` entry in that same change, not a comment on each call.

### Docstrings

English, Google style. The summary is one line and ends with a period. Add `Args`, `Returns`, and `Raises` when the summary does not already say them. Comments are English sentences. Tests do not get docstrings.

### Types

pyright stays in `standard` mode, and these are errors: unknown parameter, argument, lambda, variable, and member types, and a `type: ignore` that suppresses nothing. A public function annotates every parameter and the return. Use `X | None`, `list[str]`, and `dict[str, int]`. `Any` is only for a boundary with an untyped object, and the line says which one. A `type: ignore` names one code and the reason. Do not disable a check for a whole file.

`multiprocessing.Pipe` on Windows returns `PipeConnection`, which is not a `Connection`. The type of one end is a Protocol with `send`, `recv`, `poll`, and `close`. `recv` returns `Any`; the line says it is an unpickled object from the other process.

### Errors

Hard failures raise. `InputError` is a bad argument or a missing path. `OperationError` is a failure while doing the work. Both subclass `PytoolboxError`. Messages are English.

A batch that can fail on one item and continue returns `BatchResult`: how many succeeded, the failures (`path` and `message`), and `cancelled`. If the output directory itself cannot be created, raise `OperationError` and write nothing. Do not report that as an item failure.

A long batch yields `Progress` (`completed`, `total`, `path`) and yields one `BatchResult` last. No progress callback. A session exposes start, stop, and `LogEvent` values (`time` in timezone-aware UTC, `message`). Do not add a logging framework.

A long call takes `cancel: Callable[[], bool] | None = None` and checks it between items. On cancel, return what finished with `cancelled=True`.

The types live in `pytoolbox.errors` and `pytoolbox.results`. A failure is a `PytoolboxError` subclass, not a new base.

### Safeguards

A tool that writes or deletes takes `--dry-run`, holds a lock on each write target, and records a history entry. A read-only tool has none of the three.

- The lock is an OS file lock (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere) on `<state>/locks/<sha256 of the resolved target>.lock`, so a crashed process releases it. If the lock is held, raise `OperationError` and write nothing.
- History is SQLite at `<state>/history.sqlite3`. One row per run that is not `--dry-run`: tool, arguments as JSON, start and end in UTC ISO 8601, success count, failure count, cancelled, and the error message if it raised.
- `<state>` is `%LOCALAPPDATA%\pytoolbox` on Windows and `$XDG_STATE_HOME/pytoolbox` elsewhere, with `~/.local/state` when the variable is unset. Tests pass a `tmp_path` state directory.

### Streaming

Only batch `Progress`, UDP `LogEvent`, and the two folder walks below stream. The CLI writes one line per value to stdout. The GUI sends them over SSE. Other tools return one result. A stream yields only `Progress`, `LogEvent`, and a final `BatchResult`; the GUI ends a run that yields anything else as an unexpected failure.

One exception: `hardcoding` walks a folder and streams. Its library function counts the files first, then yields `Progress` per file, one finding per hit as it is found, and one result last: the count of each kind, the unreadable files as `ItemFailure`, and `cancelled`. Findings are not collected in memory: a large tree had 4 million. The CLI prints each finding as it comes and no progress. The GUI keeps the first 1,000 findings for its table and sends `progress` and the final `result` over SSE. A final result that is not a batch is a `Report` (`pytoolbox_gui.sse`): a template and its context, sent as the `result` event. Starting a scan in the GUI stops the scan started before it, so a forgotten scan does not hold a CPU. Recorded in [ADR 0013](adr/0013-hardcoding.md).

`folder-size` also walks a folder and streams. Its library function lists the folder's direct items, then sums each direct folder in turn and yields `Progress` after each one, and one result last: the items, largest first, the count of unreadable items, and `cancelled`. A cancelled result holds only the direct files, links, and the folders that were summed whole. The CLI writes progress to stderr when it is a terminal. The GUI shows the first 1,000 rows and sends the result as a `Report`. Starting a folder-size run in the GUI stops the one before it, as `hardcoding` does. Recorded in [ADR 0014](adr/0014-folder-size.md).

### Subprocess

Only the runner starts a process: an external command or a script under `native-scripts/`. It takes an argument list, never a shell string. A script is a tool's implementation. No CLI command or GUI screen runs a script by name or path. On `cancel`, the runner terminates the child and returns with `cancelled=True`. A Python tool runs in process.

One exception: regular expressions are matched in a worker process, not in process and not through the runner. A `re` match runs in C and checks neither `cancel` nor Ctrl+C, and a thread stuck in one cannot be stopped, so a pattern such as `(a+)+$` would hold a CPU until the process exits. The worker is a `multiprocessing` process.

- The GUI keeps one worker for its next match. A match that takes more than 1 second is `OperationError` (`Timed out after 1 s.`); the GUI terminates the worker and starts a new one for the next match. Stopping the server terminates the worker.
- The CLI starts a worker for its one match and waits with no limit. On Ctrl+C it terminates the worker and exits `130`.

Recorded in [ADR 0010](adr/0010-regex-worker.md).

### Files

- Take and return `pathlib.Path`. Do not follow symlinks or junctions.
- A written path must resolve inside the directory the caller named. Refuse the rest.
- Text files are UTF-8 without a BOM. A CSV meant for Excel is UTF-8 with a BOM.
- Do not overwrite an existing path unless the caller passes `overwrite=True` (`--overwrite` on the CLI).
- `--dry-run` writes nothing and reports the paths it would write.
- `shell=True` is forbidden. Pass an argument list.
- Name the exception you catch. A bare `except` is a lint failure.

### Tests

Call a pure function directly. Files use pytest `tmp_path`. A unit test does not open a socket or the network. Branch coverage stays at least 90%.

A test never starts the real GUI server: `pytoolbox gui` tests replace `serve`, and `serve` tests replace `uvicorn.Server` and the port check. The server is tested through `TestClient` with `base_url="http://127.0.0.1:8000"`.

### CLI

argparse. Exit `0` on success, `1` on `InputError`, `2` on `OperationError`. One level: `pytoolbox <tool>`, with no system prefix (`docs/design.ja.md` §6). `pytoolbox list` prints each tool with its system and category (§5.1). A tool row adds its CLI command and its GUI screen in the same PR. The GUI listens on `127.0.0.1:8000` (§7.2).

A usage error is `InputError` and exits `1`; the parser does not use argparse's own exit `2`. Ctrl+C exits `130` (128 plus `SIGINT`) with no traceback. Errors print `error: <message>` to stderr. Each subcommand names its handler with `set_defaults(handler=...)`, and `main` calls it with the parsed arguments; there is no fallback command. A subcommand's name and help come from the tool's constant in `registry`.

A text tool takes its text as an optional argument. Without it, the tool reads standard input as UTF-8 bytes, drops a leading BOM, and counts the rest as given, line breaks included. Input that is not UTF-8 is `InputError`. An empty argument is empty text, not a request to read standard input.

`pytoolbox text-count` prints `total`, `non-space`, and `half-width`, one per line, each label padded to the longest and followed by two spaces and the number.

`pytoolbox regex PATTERN [TEXT] -g -i -m` prints each match as `N  start-end  <JSON string>`, then each group under it, indented two spaces: `N  start-end  <JSON string>`, or `N name  start-end  <JSON string>` when the group is named, or `N  unmatched` when it took no part in the match. Positions count code points; `end` is exclusive. No matches print nothing and exit `0`. `run` sets stdout and stderr to UTF-8, so a match that is not in the console's code page still prints.

`pytoolbox resx-compare FILE1 FILE2 [--all]` prints one line per key that differs, in key order: `status  <JSON key>  <JSON value 1>  <JSON value 2>`, separated by two spaces. `status` is `differ`, `only1`, or `only2`, and the missing side's value is `null`. `--all` also prints the keys that match, with `same`. Keys sort by code point. Differences still exit `0`. Recorded in [ADR 0011](adr/0011-resx-compare.md).

`pytoolbox hardcoding DIR [--ext EXT] [--exclude NAME] [--kind KIND]` prints one line per finding: `path:line  kind  <JSON string>`, separated by two spaces. `path` is relative to `DIR` with `/` separators, and `kind` is `string`, `number`, `ip`, `url`, or `email`. Each option takes a comma-separated list and can repeat. `--ext` defaults to `.cs,.vb,.js,.py`; the leading `.` is optional and case does not matter. `--exclude` names folders, matched whole and ignoring case, and defaults to `node_modules,bin,obj,.git,.venv`. `--kind` defaults to all five; an unknown kind is `InputError`. Files go in path order, findings in line order, then by position and kind. An unreadable file prints `skipped: path: reason` to stderr. Findings still exit `0`; Ctrl+C exits `130`.

`pytoolbox folder-size [FOLDER] [--top N] [--csv]` sizes the current folder when `FOLDER` is left out. It prints one line per direct item, largest first: `size  kind  name`, with sizes right-aligned and kinds padded to the longest, then a blank line and `total: <size> (<n> items)`, and `unreadable: <n>` when any item could not be read. `kind` is `folder`, `file`, or `link`; a link or junction is not followed and has size 0. Sizes are logical sizes, as Explorer's "Size" shows them, in 1024 steps: `B` as a whole number, then `KB`, `MB`, `GB`, and `TB` with one decimal. `--top N` prints the first `N` items and `... N more` for the rest. `--csv` prints CSV instead of the table: UTF-8 with a BOM, CRLF line ends, the header `name,kind,bytes,size`, and every item, whatever `--top` says. The tool writes no file; `> result.csv` saves it. The CSV goes to `sys.stdout.buffer` as bytes: Windows text mode turns each `\n` into `\r\n`, so writing the CRLF text through `sys.stdout` would give CR CR LF. Any output with its own line ends is written the same way. When stderr is a terminal, progress, `folders done/total`, is redrawn on one stderr line and cleared before the output. A cancelled walk ends the table with `cancelled`. Ctrl+C exits `130`.

`pytoolbox list` prints the system at column 0, the category at 2, and `name  summary` at 4, with names padded to the longest in the category. Systems follow `System`; categories and tools follow `TOOLS`. An empty registry prints nothing and exits `0`.

`pytoolbox gui` is the only command that starts the GUI. It imports `pytoolbox_gui` when called, so the core does not depend on it. If the package is missing, it prints the install command of §7.1 to stderr and exits `2`. If the port is in use, it exits `2` and opens no browser.

### GUI HTTP

The GUI is a hypermedia API: it returns HTML for HTMX to swap, and its endpoints follow what the screens need. There is no JSON data API; programs use the CLI and the library. Sources: [Hypermedia APIs vs. Data APIs](https://htmx.org/essays/hypermedia-apis-vs-data-apis/), [htmx documentation](https://htmx.org/docs/), [RFC 9110 §9.2.1](https://www.rfc-editor.org/rfc/rfc9110#section-9.2.1).

| Request                   | Does                                                                 |
| ------------------------- | -------------------------------------------------------------------- |
| `GET /`                   | The shell page: sidebar, and the home cards in the pane              |
| `GET /search?q=<text>`    | The home cards of the tools that match. `HX-Request` gets the pane   |
| `GET /tools/<tool>`       | The tool's page. A request with `HX-Request` gets only the pane      |
| `POST /tools/<tool>`      | Runs a tool that returns at once, and returns the result fragment    |
| `POST /tools/<tool>/runs` | Starts a streaming run, and returns a fragment that holds the run ID |
| `GET /runs/<id>/events`   | The run's SSE stream                                                 |
| `POST /runs/<id>/cancel`  | Sets the run's `cancel`. Returns `204`                               |
| `GET /static/<file>`      | Assets shipped in the package                                        |

- Paths use the tool's CLI name. No system prefix and no `/api`.
- `GET` never runs a tool or changes state; browsers prefetch and follow links. Every run is `POST`, even for a read-only tool.
- A Developer Utilities screen posts its input on every `input` event with `hx-sync="this:replace"`, so a request still in flight is replaced and the last keystroke always wins; htmx's default `drop` would discard it. The search box uses the same `hx-sync`. Source: [htmx hx-sync](https://htmx.org/attributes/hx-sync/).
- A screen with more than one field posts the form, not one control, on `input` and on `submit` (`hx-trigger="input, submit"`), so Enter does not leave the page. The regex screen is that form: the pattern, `g` `i` `m`, and the text.
- The resx-compare screen is the exception to posting on `input`, because a half-typed path does not exist. It has no button and posts the form when a path is committed: on `change` (leaving the field) and on Enter (`hx-trigger="change, keyup[key=='Enter']"`). A form with two text fields and no submit button does not submit on Enter ([HTML: implicit submission](https://html.spec.whatwg.org/multipage/form-control-infrastructure.html#implicit-submission)), so `submit` would not fire. While a path is empty, the handler compares nothing and returns the result that asks for both paths, not an error. The client does not filter the trigger: in htmx 2.0.11 on Edge, a filter that reads a property chain, such as `change[target.value]` or `change[this.elements.file1.value]`, never passed, while `keyup[key=='Enter']` does. The GUI strips surrounding spaces and one pair of surrounding double quotes from each path, which Explorer's "Copy as path" adds. A relative path is `InputError`, because the server's working directory is not visible on the screen.
- A successful result fragment includes an empty `#error` with `hx-swap-oob="true"`, so a previous 422 or 500 does not stay after the next good input. The initial page does not include it. While `#error` holds an error, `.tool-result` is dimmed, so the last good result is not read as the current one.
- A browser submits a textarea's line breaks as CRLF. The handler reads text fields with `screens.text_field`, which turns them back into LF, so a tool counts what the box shows. Source: [HTML: converting an entry list to a list of name-value pairs](https://html.spec.whatwg.org/multipage/form-control-infrastructure.html#converting-an-entry-list-to-a-list-of-name-value-pairs).
- Form fields carry the CLI options in `snake_case` (`--dry-run` is `dry_run`) and are form-encoded, the HTMX default. The handler calls the same library function as the CLI. Paths are typed as text; the server reads the local disk, so there is no upload.
- An unknown tool on `GET` returns `404`. A `POST` to the endpoint of the other kind (`/tools/<tool>` for a streaming tool, or the reverse) is `InputError`.
- `InputError` returns `422` and `OperationError` returns `500`, each with an error fragment. Any other exception returns `500` with a generic message and is logged. The shell page sets `htmx-config` `responseHandling` so `422` and `500` swap into the error area; HTMX does not swap `4xx` and `5xx` by default.
- SSE is a `StreamingResponse` with `text/event-stream` and `Cache-Control: no-cache`. Each event is named, and its data is one HTML fragment: `progress` for `Progress`, `log` for `LogEvent`, `result` for the final result, `error` for a failure. The last event is `done` with the data `done`, and the page closes the stream with `sse-close="done"`; a browser does not dispatch an event whose data is empty. A run has one reader. Each `sse-swap` part of `run.html` sets `hx-target="this"`: an `sse-swap` element follows an `hx-target` it inherits, so under a form with `hx-target` the first event replaced the whole run and closed the stream (seen in Edge with the SSE extension 2.2.4; its documentation does not say). Starting a run clears `#error`. A result that offers a CSV embeds it as a `data:text/csv;charset=utf-8,` link with `download`, so the browser saves it and the server writes nothing and needs no download endpoint; Edge saved 20,000 rows that way with the BOM intact. Sources: [htmx SSE extension](https://htmx.org/extensions/sse/), [WHATWG server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html).
- Run IDs are `uuid4` hex. Runs live in memory. An unknown ID returns `404`. Closing the page does not cancel a run; `POST /runs/<id>/cancel` does. Stopping the server cancels every run.
- Every request's `Host` must be `127.0.0.1:<port>` or `localhost:<port>`, or it returns `403`. This stops DNS rebinding. Every `POST` must carry an `Origin` of `http://127.0.0.1:<port>` or `http://localhost:<port>`; a missing or `null` `Origin` returns `403`. This stops other sites from posting to the server. No CORS headers are sent. Sources: [GitHub: Localhost dangers](https://github.blog/security/application-security/localhost-dangers-cors-and-dns-rebinding/), [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).
- HTMX and its SSE extension ship under `static/` at pinned versions, with the version in the file name (`htmx-2.0.11.min.js`, `htmx-ext-sse-2.2.4.min.js`). A new version is a new file whose SHA-384 matches the integrity value in the htmx docs. No CDN: the GUI has tools for a broken network, and it must load without one. Nothing in the frontend is built.
- The CSS is one hand-written file, `static/app.css`, in plain CSS: custom properties for colors and spacing, grid for the layout, nesting where it helps. No CSS framework and no utility classes. Class names describe the part (`sidebar`, `pane`, `console`), not the look.
- The look follows `docs/design.ja.md` §5: the OS light or dark setting through `light-dark()`, a sidebar that collapses to its icons, and cards on the home page.
- Search matches `q` against each tool's name and summary, case-insensitive, as a substring, with surrounding spaces ignored. An empty `q` returns every tool. The search box sends it with `hx-get` on `input changed delay:200ms, keyup[key=='Enter']`, so typing sends one request per pause and no script is written by hand. Search is a `GET` because it runs nothing. Source: [htmx Active Search](https://htmx.org/examples/active-search/).
- Icons are SVG files from [Fluent UI System Icons](https://github.com/microsoft/fluentui-system-icons) (MIT), size 20, `regular`, copied to `static/icons/` under their upstream file names (`ic_fluent_home_20_regular.svg`) with the upstream `LICENSE` beside them. They come from commit `4c94ad5f4081168c0d45bdc368f230ecb757cd09`; a newer icon is copied from a named commit, and this line is updated. Only icons in use are copied. A tool's icon is `Screen.icon`, the name between `ic_fluent_` and `_20_regular`. CSS draws an icon as a `mask` filled with `currentColor`, so it follows the theme.
- Tests use `TestClient` and send `Host` and `Origin` as a browser would. A test also checks each `403` case.

`cache-clean` takes one or more target names and touches only those names. No name, or an unknown name, is `InputError` and scans nothing. The default and `--dry-run` report paths and byte counts. `--apply` deletes. Paths and ages are in `docs/design.ja.md` §9.

Generators (`csv-gen`, `json-gen`, `mock-image`) take an optional `seed: int | None`. Omit it and the output is not deterministic.

```python
def count_files(root: Path) -> int:
    """Return how many files are under root.

    Args:
        root: Directory to walk. Links are not followed.

    Returns:
        The number of files.
    """
```
