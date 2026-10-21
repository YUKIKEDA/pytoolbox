# 0001. Development toolchain

- Status: Accepted
- Date: 2026-10-21
- Issue: none. The owner ordered the development setup in chat, before this repository had Issues.

## Context

The repository had a draft spec and an empty `.gitignore`. Reference projects (Graft, Txfio, interpx, convx, gprx) gate work with a task runner, an agent entry file, and Cursor rules. This repository is Python. The machine has Python 3.10 and 3.11 on PATH, and uv can install a newer interpreter.

## Decision

- uv workspace, root not published.
- Python 3.12 is the minimum. uv installs it; the system interpreter is not the toolchain.
- ruff formats and lints. pyright type-checks in `standard` mode. pytest measures branch coverage, and `just coverage` fails under 90%.
- `just lint`, `just test`, and `just coverage` are the gates.
- One workspace member exists today: `packages/pytoolbox`, with a version constant so the gates run. The GUI package waits for its Issue.

## Consequences

Raising the pyright mode or the coverage threshold is a new Issue. Adding a dependency the draft does not name is a design change first.
