# Conventions

Agent entry: [AGENTS.md](../AGENTS.md). There is no `CONTRIBUTING.md`.

## Layout

uv workspace. The root project is not published (`tool.uv.package = false`).

```text
packages/pytoolbox/     core library. CLI comes with the Issue that adds it
packages/<name>/tests/  tests for that package
docs/design.ja.md       the owner's draft spec, Japanese
docs/roadmap.md         ID, title, Issue, status
docs/adr/               one decision per file, NNNN-slug.md
native-scripts/         created with the Issue that adds a shell script
.dev/                   ignored local notes
```

`packages/pytoolbox-gui` is named in the draft and is not a workspace member yet. Add it in the Issue that builds it. Do not add an empty module, recipe, or script ahead of that Issue.

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

### Errors

Hard failures raise. `InputError` is a bad argument or a missing path. `OperationError` is a failure while doing the work. Both subclass `PytoolboxError`. Messages are English.

A batch that can fail on one item and continue returns `BatchResult`: how many succeeded, the failures (`path` and `message`), and `cancelled`. If the output directory itself cannot be created, raise `OperationError` and write nothing. Do not report that as an item failure.

A long batch yields `Progress` (`completed`, `total`, `path`) and yields one `BatchResult` last. No progress callback. A session exposes start, stop, and `LogEvent` values. Do not add a logging framework.

A long call takes `cancel: Callable[[], bool] | None = None` and checks it between items. On cancel, return what finished with `cancelled=True`.

The types are created in M1-1, the foundation. Do not add an empty module ahead of that.

### Safeguards

A tool that writes or deletes takes `--dry-run`, holds a lock on each write target, and records a history entry. A read-only tool has none of the three.

- The lock is an OS file lock (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere) on `<state>/locks/<sha256 of the resolved target>.lock`, so a crashed process releases it. If the lock is held, raise `OperationError` and write nothing.
- History is SQLite at `<state>/history.sqlite3`. One row per run that is not `--dry-run`: tool, arguments as JSON, start and end in UTC ISO 8601, success count, failure count, cancelled, and the error message if it raised.
- `<state>` is `%LOCALAPPDATA%\pytoolbox` on Windows and `$XDG_STATE_HOME/pytoolbox` elsewhere, with `~/.local/state` when the variable is unset. Tests pass a `tmp_path` state directory.

### Streaming

Only batch `Progress` and UDP `LogEvent` stream. The CLI writes one line per value to stdout. The GUI sends them over SSE. Other tools return one result.

### Subprocess

Only the runner starts a process: an external command or a script under `native-scripts/`. It takes an argument list, never a shell string. A script is a tool's implementation. No CLI command or GUI screen runs a script by name or path. On `cancel`, the runner terminates the child and returns with `cancelled=True`. A Python tool runs in process.

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

### CLI

argparse. Exit `0` on success, `1` on `InputError`, `2` on `OperationError`. One level: `pytoolbox <tool>`, with no system prefix (`docs/design.ja.md` §6). `pytoolbox list` prints each tool with its system and category (§5.1). A tool row adds its CLI command and its GUI screen in the same PR. The GUI listens on `127.0.0.1:8000` (§7.2).

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
