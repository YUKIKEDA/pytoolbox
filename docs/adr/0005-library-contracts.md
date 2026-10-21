# 0005. Library contracts

- Status: Accepted
- Date: 2026-10-21
- Issue: #1, added while that pull request was open. The owner said these elementary choices were to be made, not listed.

## Context

Types stopped at pyright `standard` and annotated signatures. Errors stopped at "no bare except". Behavior that differed between the old tools was still marked open in `docs/design.ja.md`.

## Decision

Unknown types are errors. Failures that stop the call raise `InputError` or `OperationError`. A batch that can continue returns `BatchResult`. The other contracts, including the old tools' differences, are written in `docs/conventions.md` and `docs/design.ja.md`. Implementation order is the only item left to a roadmap row.

## Consequences

The first tool's change adds the exception and result types. It does not add them empty ahead of that change.
