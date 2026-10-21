[English](README.md) | 日本語

# pytoolbox

個人用の Python ツールボックス。コアはライブラリと CLI で、Web GUI は隣に置く予定である。所有者以外の Issue と Pull Request は受け付けない。

仕様は草案である: [docs/design.ja.md](docs/design.ja.md)。design Issue が契約だと書くまで、契約ではない。作業の列は [docs/roadmap.md](docs/roadmap.md) にある。

## Develop

[uv](https://docs.astral.sh/uv/) が Python 3.12 を入れる。[just](https://just.systems/) がゲートを走らせる。

```powershell
uv sync --all-packages --group dev
just lint
just test
just coverage
```

エージェントの入口: [AGENTS.md](AGENTS.md)。ライセンス: [MIT](LICENSE)。
