English | [日本語](README.ja.md)

# pytoolbox

A personal Python toolbox: a core library and CLI, with a web GUI planned beside it. Outside issues and pull requests are not accepted.

The spec is a draft: [docs/design.ja.md](docs/design.ja.md). It is not a contract until a design Issue says so. Open work is listed in [docs/roadmap.md](docs/roadmap.md).

## Develop

[uv](https://docs.astral.sh/uv/) installs Python 3.12. [just](https://just.systems/) runs the gates.

```powershell
uv sync --all-packages --group dev
just lint
just test
just coverage
```

Agent entry: [AGENTS.md](AGENTS.md). License: [MIT](LICENSE).
