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

## Packages

- Public functions have type annotations and an English docstring.
- Tests live beside the package, under `tests/`, and are named `test_<behavior>`.
- `shell=True` is forbidden. Pass an argument list.
- A destructive system action has a dry-run path before it ships. The draft states this; the design Issue decides the flag.
