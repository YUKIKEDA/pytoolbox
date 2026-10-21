# 0002. Initial tool list, UI stack, and license

- Status: Accepted
- Date: 2026-10-21
- Issue: none. The owner chose these in chat after the convention bootstrap.

## Context

The owner's draft, `docs/design.ja.md`, sets the concept and the architecture. The owner also has six existing tools, and PyDevToys already hosts most of them behind an eel window. The repository is public.

## Decision

- The initial tool list is built from those six tools, in `docs/design.ja.md` §9. It does not narrow the concept or the architecture of the draft.
- The GUI is the draft's local web app: FastAPI, Jinja2, HTMX, and Tailwind. The same Python core serves the CLI and the GUI. eel and per-tool tkinter windows are not the UI.
- The license is MIT, in `LICENSE`.

## Consequences

Dependencies named by the GUI stack are added in the Issue that builds the GUI, not before. A change to the draft goes through a Grill.
