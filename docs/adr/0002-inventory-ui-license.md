# 0002. Tool inventory, UI stack, and license

- Status: Accepted
- Date: 2026-10-21
- Issue: none. The owner chose these in chat after the convention bootstrap.

## Context

`docs/design.ja.md` sketched DevToys-style text tools and PC maintenance tasks. The tools the owner wants in the box are the six existing projects, and PyDevToys already hosts most of them behind an eel window. The repository is public.

## Decision

- The tool list is the inventory in `docs/design.ja.md`. A capability that is not in that list is not in the product until a later decision adds it.
- The GUI is a local web app: FastAPI, Jinja2, HTMX, and Tailwind. The same Python core serves the CLI and the GUI. eel and per-tool tkinter windows are not the UI.
- The license is MIT, in `LICENSE`.

## Consequences

Command names, unresolved behavior differences inside one tool, and implementation order are still open. They are not settled by this ADR. Dependencies named by the GUI stack are added in the Issue that builds the GUI, not before.
