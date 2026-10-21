# 0009. GUI startup, HTTP, and assets

- Status: Accepted
- Date: 2026-10-22
- Issue: #16

## Context

M1-1 builds the GUI shell. The draft names FastAPI, Jinja2, HTMX, and Tailwind, two start commands (`pytoolbox gui` and `pytoolbox-gui`), and JSON and SSE endpoints split by system (`/api/dev/...`, `GET /api/system/run/...?dry_run=true`). It does not name a server, a test client, or how the server is protected. The GUI runs tools that write and delete files, on `127.0.0.1`.

## Decision

- uvicorn serves the app. FastAPI alone does not listen on a port.
- `httpx` is a dev dependency, for FastAPI's `TestClient`.
- `pytoolbox gui` is the only start command. A CLI-only install still works, and the user remembers one command. `uv tool install` exposes only the named package's commands, so the full install names `pytoolbox` and adds the GUI with `--with`.
- The sidebar is drawn from the tool registry and shows only categories that have tools. An entry must open a working screen.
- The server is a hypermedia API that returns HTML. HTMX swaps HTML, so a JSON data API would add a second contract with no consumer. Paths use tool names, because streaming depends on the tool, not the system: mock-image streams progress and sits under Developer Utilities.
- Only `POST` runs a tool. `GET` is safe by HTTP semantics, and browsers prefetch and follow links. A streaming run returns an ID; SSE streams it; a `POST` cancels it. Cancel is required for UDP stop and for batch cancel.
- Every request is checked for a loopback `Host`, and every `POST` for a loopback `Origin`. A page on another site can post to a loopback server, and DNS rebinding can read from it.
- HTMX and the CSS ship in the package. The GUI has tools for a broken network, so it must load without one. Nothing in the frontend is built, because a frontend build would remove the reason to use HTMX.
- The CSS is one hand-written file, `static/app.css`, in plain CSS that browsers read as is (custom properties, grid, nesting). It replaces Tailwind. Tailwind needs a build, and its browser build is for development only and compiles in the browser on every load and swap. A ready-made framework such as Bootstrap is large and class-heavy, and the DevToys layout would still be written by hand. Pico CSS is no longer maintained (v2.1.1 is final). The screens are a sidebar, two text areas, a form, and a console: small enough to style by hand, with no dependency that can stop being maintained.

## Consequences

`docs/design.ja.md` §3, §5.1, §5.2, §6, §7.1, and §7.2 carry these decisions. `docs/conventions.md` holds the HTTP rules and their sources.
