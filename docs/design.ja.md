> 状態: 草案。ツール一覧（§9）、GUI、型、エラー、CLI は 2026-10-21 に決めた。実装順だけが `docs/roadmap.md` の行待ちである。契約にする変更は design Issue で行う。

# pytoolbox と pytoolbox-gui

## 1. 概要

日常のファイル操作、テキスト処理、通信確認を一つの Python 基盤にまとめる。コア `pytoolbox` はライブラリと CLI。`pytoolbox-gui` はそのコアをローカルブラウザに出す。

機能の一覧は §9 である。草案の初期案にあった Base64、JWT、JSON 整形、UUID、パスワード、ハッシュ、キャッシュ掃除、Wi-Fi リセット、環境キッティング、実行履歴 DB、ログイン常駐は、この一覧に無い。一覧へ足すには別の決定が要る。

実装は Python を原則とする。既存ツールにシェル専用の処理は無い。`native-scripts/` は、必要だと Issue が言うまで作らない。

## 2. 実行パス

ツールは操作の形で3つに分かれる。コアの関数は CLI と GUI の両方から呼ぶ。

```text
[ 操作 ]
   ├── 即時     入力を受け取り、結果を返す
   ├── バッチ   ファイルを読む・書く、または走査する。進捗を返してよい
   └── セッション  開始と停止があり、ログを流し続ける
```

GUI はローカルの Web アプリである。FastAPI が API を出し、Jinja2 が HTML を出し、HTMX と Tailwind で画面を組む。待たない処理は通常のリクエストで返す。バッチの進捗とセッションのログは、そのツールを作る Issue が配信方法を決める。全ツールに SSE、排他ロック、SQLite 履歴を先に付けない。

## 3. リポジトリ

uv のワークスペース。公開しないルートと、コアのパッケージを置く。GUI パッケージは、それを作る Issue で足す。

```text
pytoolbox/
├── pyproject.toml
├── justfile
├── packages/
│   └── pytoolbox/          コア。ツールのモジュールは、そのツールの Issue で足す
└── packages/pytoolbox-gui/ GUI。未作成
```

開発のゲートと規約は `docs/conventions.md` と `AGENTS.md`。

## 4. 実装の規則

型、エラー、ファイル、テスト、CLI の決めは `docs/conventions.md` と `docs/adr/0005-library-contracts.md`。ここには実装が従う形だけを書く。型のモジュールは、最初のツールの変更で作る。

- 未知の型は pyright が落とす。`Any` と `type: ignore` は、その行に理由があるときだけ。
- 中断する失敗は `InputError` か `OperationError`。メッセージは英語。項目ごとの失敗は続け、`BatchResult` に載せる。出力先が作れないときは何も書かず `OperationError`。
- 長い処理は `Progress` を順に返し、最後に `BatchResult` を返す。コールバックと logging は使わない。中断は `cancel` を項目のあいだで見る。
- リンクは追わない。書き先は指定ディレクトリの中だけ。テキストは UTF-8。Excel 向け CSV だけ BOM。既存パスは上書きしない（`--overwrite`）。`--dry-run` は書かない。
- 単体テストはネットワークを開かない。ファイルは一時ディレクトリ。
- CLI は argparse。終了コードは 0、入力エラー 1、実行エラー 2。

## 5. GUI

左にツール、右にそのツールの画面。カテゴリは §9 の見出しに合わせる。

- 即時の画面は、入力と結果を同じ画面に置く。
- バッチの画面は、引数、実行、結果または進捗を置く。
- セッションの画面は、開始、停止、ログを置く。
- 画像クリップは、範囲を選ぶプレビューのあと、バッチとしてフォルダへ適用する。コアへ渡す矩形は画像のピクセル座標で、画像の範囲へ丸める。

## 6. CLI

```text
pytoolbox <command> [args]
pytoolbox gui
```

`gui` は `127.0.0.1:8000` で待つ。コマンド名は次のとおり。

`text-count`、`regex`、`resx-compare`、`hardcoding`、`ping`、`port`、`folder-size`、`csv-gen`、`json-gen`、`mock-image`、`file-copy`、`folder-copy`、`pdf-split`、`file-merge`、`image-clip`、`udp`。

`csv-gen`、`json-gen`、`mock-image` は種を任意で受ける。省略すると結果は実行ごとに変わる。

## 7. 配布

コアだけ、または GUI 込みを、uv の tool install で入れる。GUI は `127.0.0.1:8000` で待ち、ブラウザを開く。

ライセンスは MIT（`LICENSE`）。

## 8. 実装順

実装順は `docs/roadmap.md` の行が決める。今は行が無い。§9 の並びは優先度ではない。

## 9. ツール一覧

出典は次の6つ。PyDevToys が eel で大半を既にまとめており、FolderSizer だけがそこへ入っていない。設定画面はチェックボックスだけで動作が無いので、一覧に入れない。

出典の違いは次のとおり決めた。

### 9.1 即時

| ツール | すること | 出典 | 決定 |
| --- | --- | --- | --- |
| 文字数 | 総文字数、空白を除いた数、半角の数を数える | PyDevToys の画面だけ。Python コアは無い | 半角は East Asian Width の H と Na。総数はコードポイント数。空白は `str.isspace` |
| 正規表現 | パターンと文字列を照合する。フラグは g / i / m | PyDevToys の画面だけ。ブラウザ内で実行している | `i` は `IGNORECASE`、`m` は `MULTILINE`、`g` は全一致。`g` が無ければ最初の一致 |
| リソース比較 | 2つの `.resx` の `data` 名を比べ、一致・不一致・片方のみを返す | PyDevToys | 無し |
| ハードコーディング検出 | ディレクトリを拡張子で歩き、文字列、4桁以上の数値、IP、URL、メールを行番号付きで返す。コメント行は飛ばす | PyDevToys | 無し |
| 疎通確認 | `ping` を1回送る。TCP ポートへ接続し、開閉と応答時間を返す | PyDevToys。引数は配列渡し | コマンドは `ping` と `port` |

### 9.2 バッチ

| ツール | すること | 出典 | 決定 |
| --- | --- | --- | --- |
| フォルダサイズ | 直下のファイルとフォルダを大きい順に出す。フォルダは中身の合計。リンクとジャンクションは追わない。読めない項目は件数だけ報告する。`--top` は表示件数、CSV は全件、UTF-8 BOM | FolderSizer。PyDevToys には無い | 無し |
| CSV 生成 | 列の型（text, number, boolean, date）と行数から CSV を書く | PyDevToys | 種は任意。Excel 向けなので BOM 付き |
| JSON 生成 | プロパティ定義と件数から JSON を書く。型は string, number, boolean, date, email, uuid, url, ip, path, array, object, date_range, custom_list | PyDevToys | 種は任意 |
| モック画像 | 幅、高さ、枚数、出力先から、寸法を描いた画像を書く。比率をランダムにする指定がある | PyDevToys | 種は任意 |
| ファイルコピー | 同じ内容を、書式で連番にした名前で複数書く | PyDevToys | 無し |
| フォルダコピー | フォルダを、名前と番号の書式で複数コピーする | PyDevToys | 無し |
| PDF 分割 | PDF を1ページずつ `page_N.pdf` として書く | PDFDivider と PyDevToys | 無し |
| ファイル結合 | 選んだテキストを、区切り付きで1つの `.txt` に書く。読めないファイルはその旨を書く | FileMergerGUI と PyDevToys | 名前は `merged_YYYYMMDD_HHMMSS.txt` |
| 画像クリップ | フォルダ内の jpg, jpeg, png, bmp, gif を、指定した矩形で切り、`cropped_` を付けて書く。元フォルダへ書く選択がある。JPEG 品質 95 | PyImageCripper と PyDevToys | 矩形は画像のピクセル座標。画像の外は丸める |

### 9.3 セッション

| ツール | すること | 出典 | 決定 |
| --- | --- | --- | --- |
| UDP 受信 | 指定したアドレスで UDP を待ち、届いたデータグラムをログする。開始と停止がある | UDPListener と PyDevToys | 既定は `127.0.0.1:9000`。長さ、16進、UTF-8 で読めた文字列をログする |
