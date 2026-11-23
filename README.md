English | [日本語](README.ja.md)

# pytoolbox

A personal Python toolbox combining a core library and CLI (`pytoolbox`) with a local web GUI (`pytoolbox-gui`). Outside issues and pull requests are not accepted.

## Quick start

```powershell
# CLI + Web GUI
uv tool install ./packages/pytoolbox --with ./packages/pytoolbox-gui

# CLI only
uv tool install ./packages/pytoolbox

pytoolbox list
pytoolbox gui
```

## Documentation

User documentation (Japanese, organized with [Diataxis](https://diataxis.fr/)):

- [Tutorials (docs/tutorials.md)](docs/tutorials.md): Installation and first steps with CLI and Web GUI
- [How-to guides (docs/how-to.md)](docs/how-to.md): Task-oriented recipes across generators, files, code checks, cleanup, and network tools
- [Reference (docs/reference.md)](docs/reference.md): Complete CLI arguments, exit codes, and Web GUI screen specifications for all 17 tools
- [Explanation (docs/explanation.md)](docs/explanation.md): Concept, two-path architecture, safety locks/history, localhost security, and out-of-scope decisions

Internal developer documentation (English):

- [Conventions (docs/conventions.md)](docs/conventions.md): Repository layout, Python style, CLI/GUI contracts, and toolchain
- [Roadmap (docs/roadmap.md)](docs/roadmap.md): Milestone order and status
- [ADRs (docs/adr/)](docs/adr/): Architecture decision records

## Develop

[uv](https://docs.astral.sh/uv/) installs Python 3.12. [just](https://just.systems/) runs the gates.

```powershell
uv sync --all-packages --group dev
just lint
just test
just coverage
```

Agent entry: [AGENTS.md](AGENTS.md). License: [MIT](LICENSE).
