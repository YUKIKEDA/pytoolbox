# Conventions

Agent entry: [AGENTS.md](../AGENTS.md). There is no `CONTRIBUTING.md`.

## Layout

uv workspace. The root project is not published (`tool.uv.package = false`).

```text
packages/pytoolbox/     core library. CLI comes with the Issue that adds it
packages/<name>/tests/  tests for that package
docs/design.ja.md       draft spec, Japanese, not a contract until a design Issue says so
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

### Types

pyright runs in `standard` mode. A public function annotates every parameter and the return. Use `X | None`, `list[str]`, and `dict[str, int]`. `Any` is a boundary with an untyped object, and the line says which one.

### Docstrings

English, Google style. The summary is one line and ends with a period. Add `Args`, `Returns`, and `Raises` when the summary does not already say them. Comments are English sentences. Tests do not get docstrings.

### Paths, processes, and failures

- Take and return `pathlib.Path` for filesystem paths.
- `shell=True` is forbidden. Pass an argument list.
- Name the exception you catch. A bare `except` is a lint failure.
- A tool that writes or deletes has a dry-run path in the same change.

```python
def count_files(root: Path) -> int:
    """Return how many files are under root.

    Args:
        root: Directory to walk. Links are not followed.

    Returns:
        The number of files.
    """
```
