[English](roadmap.md) | 日本語

# Roadmap

現在の行の ID は、このファイルにだけ書く。受け入れ条件は Issue に書く。

始めるのは現在の行だけである。`docs/design.ja.md` §8 が段を、§9 が初期ツールを名指しする。順番はこの表である。

## Current work

M1-1。

| ID | Title | Issue | Status |
| --- | --- | --- | --- |
| M1-1 | Add the foundation: error and result types, tool list, GUI shell | #12 | Next |
| M2-1 | Add text-count | #3 | Not started |
| M2-2 | Add regex | | Not started |
| M2-3 | Add resx-compare | | Not started |
| M2-4 | Add hardcoding | | Not started |
| M2-5 | Add folder-size | | Not started |
| M2-6 | Add udp | | Not started |
| M3-1 | Add the per-target lock | | Not started |
| M3-2 | Add the run history and its page | | Not started |
| M4-1 | Add csv-gen | | Not started |
| M4-2 | Add json-gen | | Not started |
| M4-3 | Add mock-image | | Not started |
| M4-4 | Add file-copy | | Not started |
| M4-5 | Add folder-copy | | Not started |
| M4-6 | Add pdf-split | | Not started |
| M4-7 | Add file-merge | | Not started |
| M4-8 | Add image-clip | | Not started |
| M4-9 | Add cache-clean | | Not started |
| M5-1 | Add the subprocess runner | | Not started |
| M5-2 | Add ping and port | | Not started |
| M6-1 | Add json-format | | Not started |
| M6-2 | Add base64 | | Not started |
| M6-3 | Add uuid | | Not started |

各マイルストーンは `docs/design.ja.md` §8 の一つの段である。ツールの行は、CLI のコマンドと GUI の画面を一緒に出す。前の行が開いているあいだ、次の行は始めない。

## Intentionally out of scope

- ネイティブスクリプトを名前やパスで実行する CLI のコマンドと GUI の画面、およびスクリプトの自動検出。スクリプトはツールの中身である（[ADR 0008](adr/0008-cli-safeguards-order.md)）。
- PC ログイン時のバックグラウンド常駐（[ADR 0008](adr/0008-cli-safeguards-order.md)）。
