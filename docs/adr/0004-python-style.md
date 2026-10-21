# 0004. Python style

- Status: Accepted
- Date: 2026-10-21
- Issue: #1, added while that pull request was open. The owner asked for formatting and lint conventions.

## Context

`just lint` ran ruff with only `E`, `F`, `I`, `UP`, `B`, `SIM`, and `RUF`. Format options, docstrings, naming, annotations, and `pathlib` were prose in a short rule. They were not a gate.

## Decision

`ruff format` is the only formatter: 100 columns, 4 spaces, double quotes, LF. `ruff check` enables the groups listed in `docs/conventions.md`. pyright stays on `standard`. Public docstrings are English and Google-style. Library code does not print. New filesystem code uses `pathlib`.

`PLR`, `FBT`, `TC`, and ruff preview rules stay off for the reasons in that file.

## Consequences

A new lint code is a change to `pyproject.toml` and to the table in `docs/conventions.md` in the same PR. Weakening the set is an ADR.
