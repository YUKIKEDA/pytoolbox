[English](README.md) | 日本語

# pytoolbox

個人用の Python ツールボックス。コアエンジン・CLI（`pytoolbox`）とローカル Web GUI（`pytoolbox-gui`）で構成する。所有者以外の Issue と Pull Request は受け付けない。

## Quick start

```powershell
# CLI + Web GUI
uv tool install ./packages/pytoolbox --with ./packages/pytoolbox-gui

# CLI のみ
uv tool install ./packages/pytoolbox

pytoolbox list
pytoolbox gui
```

## Documentation

利用者向けドキュメント（日本語・[Diátaxis](https://diataxis.fr/) 構成）:

- [チュートリアル (docs/tutorials.md)](docs/tutorials.md): 導入から CLI と Web GUI の初回実行まで
- [ハウツーガイド (docs/how-to.md)](docs/how-to.md): データ生成・ファイル／PDF／画像処理・コード検査・掃除・通信診断の目的別レシピ
- [リファレンス (docs/reference.md)](docs/reference.md): 全 17 ツールの CLI 引数・終了コード・状態保存先・Web GUI 画面仕様
- [解説 (docs/explanation.md)](docs/explanation.md): コンセプト・2 系統アーキテクチャ・安全設計・GUI セキュリティ・スコープ外の理由

開発者向け内部ドキュメント（英語）:

- [開発規約 (docs/conventions.md)](docs/conventions.md): ディレクトリ構成・Python 規約・CLI/GUI 契約・ツールチェーン
- [ロードマップ (docs/roadmap.md)](docs/roadmap.md): マイルストーン一覧と完了状況
- [意思決定記録 (docs/adr/)](docs/adr/): ADR 一覧

## Develop

[uv](https://docs.astral.sh/uv/) が Python 3.12 を入れる。[just](https://just.systems/) がゲートを走らせる。

```powershell
uv sync --all-packages --group dev
just lint
just test
just coverage
```

エージェントの入口: [AGENTS.md](AGENTS.md)。ライセンス: [MIT](LICENSE).
