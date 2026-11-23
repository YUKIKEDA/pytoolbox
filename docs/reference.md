# リファレンス

`pytoolbox`（CLI・Python ライブラリ）および `pytoolbox-gui`（Web GUI）の完全な仕様です。

---

## 1. CLI 共通仕様

- **コマンド体系**: `pytoolbox <command> [args...]` の 1 段構成（系統によるサブコマンド分割はしない）。
- **文字コード**: CLI の標準出力・標準エラー出力は UTF-8。テキスト入力ツール（`text-count`, `regex`）が標準入力を読む場合も UTF-8 として解釈し、先頭の BOM があれば取り除く（UTF-8 でない入力は `InputError`）。
- **エラー出力**: 失敗時は標準エラー出力へ `error: <message>` を出力する。`argparse` 既定の終了コード `2` は使わず、使い方誤りは `InputError`（`1`）として扱う。
- **終了コード**:
  - `0`: 正常終了（診断コマンドの到達不可・ポート閉鎖、比較差分あり、検出ヒットありを含む）
  - `1`: `InputError`（入力値の不正、未知の引数、ファイル不在、`--overwrite` なしでの既存出力衝突など）
  - `2`: `OperationError`（出力先ロック競合、外部コマンド起動失敗、待機開始失敗、GUI 起動不能など）
  - `130`: `Ctrl+C`（`128 + SIGINT`）による中断（トレースバックなし）

### 書く・消すツール共通の契約

ファイルやディレクトリを作成・上書き・削除する全ツールは以下を満たします。

1. **Dry-run**: `--dry-run`（`cache-clean` は既定値）で変更予定だけを報告し、一切書かない・消さない。
2. **排他ロック（`target_lock`）**: 書き先・削除先ルートごとのアドバイザリロックを取得し、同一対象への二重実行を `OperationError` で止める。
3. **実行履歴（`HistoryStore`）**: 実際に書く・消す実行（`--dry-run` でない実行、`cache-clean` は `--apply` のみ）を SQLite へ記録する。

### 永続化ディレクトリ（`state_dir`）

- **Windows**: `%LOCALAPPDATA%\pytoolbox`（未設定時は `~/AppData/Local/pytoolbox`）
- **Linux / macOS その他**: `$XDG_STATE_HOME/pytoolbox`（未設定時は `~/.local/state/pytoolbox`）

配下のファイル:
- `locks/<sha256>.lock`: `target_lock` が使う排他ロックファイル（Windows ではパスの大文字小文字を区別しない正規化キーから SHA-256 を算出）。
- `history.sqlite3`: 実行履歴 DB（`runs` テーブル）。

---

## 2. 管理コマンド

### `pytoolbox list`

登録済みツール（`registry.TOOLS`）を系統（`System`）ごと・カテゴリごとに表示します。系統は見出し 0 列目、カテゴリは 2 字下げ、各ツールは 4 字下げでカテゴリ内の最長ツール名に合わせて整列します。

### `pytoolbox gui`

ローカル Web サーバーを起動して `http://127.0.0.1:8000` を既定ブラウザで開きます。`pytoolbox_gui` は呼び出し時に遅延インポートされ、未インストール時は導入コマンドを標準エラー出力に出して終了コード `2` で終了します。ポート使用中もブラウザを開かず終了コード `2` で終了します。

---

## 3. 全 17 ツール仕様一覧（サイドバー順）

### Developer Utilities — Generators

#### `csv-gen`

列定義と行数から合成 CSV（UTF-8 BOM 付き）を生成します。

```text
pytoolbox csv-gen OUTPUT --rows N --col NAME:TYPE [--col ...] [--seed S] [--overwrite] [--dry-run]
```

- **型（`TYPE`）**: `text`, `number`, `boolean`, `date`
- **オプション**: `--rows`（必須）、`--col`（必須・複数回指定可）、`--seed`（任意・省略時は非決定的）、`--overwrite`、`--dry-run`
- **安全策**: `OUTPUT` に `target_lock`、非 `--dry-run` を履歴記録
- **GUI 画面**: 左に出力パス、行数、シード（任意）、列定義（1 行 1 列 `name:type`、既定は 4 列見本）、Dry-run・Overwrite、Start ボタン。右に生成・予定パスと行数を表示。

#### `json-gen`

プロパティ定義と件数から合成 JSON 配列（UTF-8 BOM なし）を生成します。

```text
pytoolbox json-gen OUTPUT --rows N [--schema PATH_OR_JSON] [--col NAME:TYPE ...] [--seed S] [--overwrite] [--dry-run]
```

- **型（13 種）**: `string`, `number`, `boolean`, `date`, `email`, `uuid`, `url`, `ip`, `path`, `array`, `object`, `date_range`, `custom_list`
- **入力**: `--schema`（スキーマ JSON ファイルパスまたはインライン JSON 文字列）または `--col NAME:TYPE`
- **安全策**: `OUTPUT` に `target_lock`、非 `--dry-run` を履歴記録
- **GUI 画面**: 左に出力パス、行数、シード（任意）、JSON スキーマ定義（既定見本入り）、Dry-run・Overwrite、Start ボタン。右に書き先・予定パスと件数を表示。

#### `mock-image`

寸法を描画した連番の PNG / JPEG モック画像を生成します（依存: `pillow>=10.1`、HPND ライセンス）。出力名は `mock_001.png` 形式。

```text
pytoolbox mock-image OUTPUT_DIR --width W --height H --count N [--random-ratio] [--format png|jpg] [--seed S] [--overwrite] [--dry-run]
```

- **オプション**: `--random-ratio`（指定 `--width`, `--height` を上限としてランダム比率化）、`--format`（既定 `png`、`png` / `jpg`）、`--seed`（任意）、`--overwrite`、`--dry-run`
- **安全策・進捗**: 出力先に一括 `target_lock`、`Progress`・`BatchResult` ストリーム、非 `--dry-run` を履歴記録
- **GUI 画面**: 左に出力フォルダ、幅・高さ・枚数、Random ratio、形式（PNG/JPEG）、シード、Dry-run・Overwrite、Start ボタン。右に進み具合と Cancel、完了後に一覧と失敗件数を表示。

---

### Developer Utilities — Text

#### `regex`

Python `re` 構文で正規表現を照合します。CLI・GUI とも別プロセスの Worker で照合します。

```text
pytoolbox regex PATTERN [TEXT] [-g | --global] [-i | --ignore-case] [-m | --multiline]
```

- **フラグ**: `-g`（全一致、省略時は最初の一致のみ）、`-i`（`IGNORECASE`）、`-m`（`MULTILINE`）
- **テキスト**: 引数省略時は標準入力から読む（空文字列引数 `""` は空文字として照合）。
- **CLI 出力**: `N  start-end  <JSON string>` の後に各グループを 2 字下げで出力（`N  start-end  <JSON string>` / 名前付き `N name  start-end  <JSON string>` / 未参加 `N  unmatched`）。位置はコードポイント数、`end` は排他。不一致時は出力なし・終了 `0`。
- **打ち切り動作**: CLI はタイムアウトせず `Ctrl+C` で Worker を停止。GUI は 1 秒で照合を打ち切る。
- **GUI 画面**: 左にパターン 1 行、`g`/`i`/`m` チェックボックス、対象テキスト。いずれかの `input` イベントで即時照合（`hx-sync="this:replace"`）。右上に色付けプレビュー、右下に一致一覧とグループ表。

#### `text-count`

文字数を数えます。

```text
pytoolbox text-count [TEXT]
```

- **集計項目**:
  - `total`: 総コードポイント数
  - `non-space`: `str.isspace()` でない文字数
  - `half-width`: Unicode East Asian Width が `H` または `Na` の文字数
- **出力形式**: 各項目を 1 行ずつ最長ラベル幅＋2 スペース区切りで出力。
- **GUI 画面**: テキストエリア入力ごとにリアルタイム更新。

---

### Developer Utilities — Code

#### `resx-compare`

2 つの `.resx` XML ファイルの `<data>` 要素を比較します。

```text
pytoolbox resx-compare FILE1 FILE2 [--all]
```

- **比較規則**: 両方にある `name` は `<value>` 文字列の完全一致（大小文字・空白区別）で判定し、`<value>` 不在と空は空文字扱い。`<comment>` と `type` は比較対象外。XML 宣言・BOM に従い、未対応文字コード（Shift_JIS 等）や不正 XML・`name` 欠落・キー重複は `InputError`。
- **CLI 出力**: キー順（コードポイント順）に `status  <JSON key>  <JSON value 1>  <JSON value 2>`。`status` は `differ` / `only1` / `only2`（`--all` で `same` も出力）。差分があっても終了 `0`。
- **GUI 画面**: 左にファイル 1・ファイル 2 の絶対パス入力欄（ボタンなし）。`change` または `Enter` で両方のパスが埋まっているときだけ実行（片方空なら案内表示、前後の空白やエクスプローラー「パスのコピー」の二重引用符を除去、相対パスは `InputError`）。右上に件数サマリ、右下に差分表を状態色分け付きで出力。

#### `hardcoding`

フォルダ内のソースからハードコーディングを検出します。

```text
pytoolbox hardcoding FOLDER [--ext EXT] [--exclude NAME] [--kind KIND]
```

- **対象拡張子（`--ext`）**: 既定 `.cs,.vb,.js,.py`（`.` 有無と大文字小文字不問、カンマ区切り・複数回指定可）
- **除外フォルダ（`--exclude`）**: フォルダ名完全一致（大小無視）、既定 `node_modules,bin,obj,.git,.venv`
- **検出種別（`--kind`）**: `string`（同一引用符 `"` / `'` の 3 文字以上かつ改行なし）、`number`（4 桁以上）、`ip`、`url`、`email`。既定は全 5 種。未知の種別は `InputError`。
- **読み取り仕様**: 先頭が `#` `//` `/*` `*` `<!--` のコメント行はスキップ。リンク・ジャンクションは追わない。UTF-8（BOM 付き可）→ 失敗時 CP932 の順で読み、読めないファイルはスキップを通知。
- **CLI 出力**: `path:line  kind  <JSON string>`（`/` 区切り相対パス、読めないファイルは stderr へ `skipped: path: reason`、終了 `0`）。
- **GUI 画面**: 左にパス、拡張子、除外、5 種別のチェックボックス、Start ボタン（Start / Enter のみ実行）。右に `Progress`・Cancel、完了後に種別件数と先頭 1,000 件の一覧表（超過時は全件を CLI で見るよう案内）とスキップファイル一覧。

---

### System Operations — Files

#### `file-copy`

1 つのファイルを連番ファイル名でコピーします。

```text
pytoolbox file-copy SOURCE DEST_DIR --count N [--format FMT] [--start N] [--overwrite] [--dry-run]
```

- **既定値**: `--format "{stem}_{n:03d}{suffix}"`、`--start 1`
- **安全策・進捗**: 展開先全パスへ一括 `target_lock`、`Progress`・`BatchResult`、非 `--dry-run` を履歴記録
- **GUI 画面**: 左に元ファイルパス、出力フォルダ、書式、開始番号、件数、Dry-run・Overwrite、Start。右に進捗・Cancel と結果一覧。

#### `folder-copy`

フォルダ全体を書式付き連番フォルダ名でコピーします。

```text
pytoolbox folder-copy SOURCE DEST_DIR --count N [--format FMT] [--start N] [--overwrite] [--dry-run]
```

- **既定値**: `--format "{name}_{n:03d}"`、`--start 1`
- **安全策・進捗**: 展開先全フォルダへ一括 `target_lock`、`Progress`・`BatchResult`、非 `--dry-run` を履歴記録
- **GUI 画面**: 左に元フォルダパス、出力先ディレクトリ、書式、開始番号、件数、Dry-run・Overwrite、Start。右に進捗・Cancel と結果一覧。

#### `pdf-split`

PDF ファイルを 1 ページずつ個別 PDF へ分割します（依存: `pypdf>=5`、BSD-3-Clause ライセンス）。

```text
pytoolbox pdf-split INPUT OUTPUT_DIR [--format FMT] [--overwrite] [--dry-run]
```

- **既定値**: `--format "page_{n}.pdf"`（`{n}` 1 始まり連番、`{stem}`, `{name}` 利用可）
- **安全策・進捗**: 全書き先へ一括 `target_lock`、`Progress`・`BatchResult`、非 `--dry-run` を履歴記録
- **GUI 画面**: 左に入力 PDF パス、出力フォルダ、ファイル名書式、Dry-run・Overwrite、Start。右に進捗・Cancel と生成ページ一覧。

#### `file-merge`

指定したテキストファイルを区切り書式付きで 1 つのファイルへ結合します。

```text
pytoolbox file-merge INPUT [INPUT ...] --output-dir DIR [--sep FMT] [--overwrite] [--dry-run]
```

- **出力**: `--output-dir` 配下の `merged_YYYYMMDD_HHMMSS.txt`（UTF-8 BOM なし）
- **既定区切り**: `--sep "--- {name} ---"`
- **エラー扱い**: 読めない入力ファイルは出力本文内に理由を注記しつつ `ItemFailure` へ記録して処理を継続
- **安全策・進捗**: 出力先へ `target_lock`、`Progress`・`BatchResult`、非 `--dry-run` を履歴記録
- **GUI 画面**: 左に入力パス複数行テキスト（1 行 1 絶対パス）、出力フォルダ、区切り書式、Dry-run・Overwrite、Start。右に進捗・Cancel と出力結果・入力別成否表。

#### `image-clip`

フォルダ内の画像を一括で矩形切り抜きし、`cropped_` プレフィックス付きで書き出します（依存: `pillow>=10.1`、JPEG 品質 `95`）。

```text
pytoolbox image-clip INPUT_DIR --box X1,Y1,X2,Y2 [--output-dir DIR] [--overwrite] [--dry-run]
```

- **対象拡張子**: `.jpg`, `.jpeg`, `.png`, `.bmp`, `.gif`
- **仕様**: `--output-dir` 省略時は `INPUT_DIR` 内へ `cropped_<元のファイル名>` として書き出し。画像サイズ外の座標は画像範囲へ丸める（クランプ）。
- **安全策・進捗**: 全書き先へ一括 `target_lock`、`Progress`・`BatchResult`、非 `--dry-run` を履歴記録
- **GUI 画面**: 左に入力フォルダと先頭画像プレビュー取得、出力フォルダ（任意）、`X1`/`Y1`/`X2`/`Y2` または `X1,Y1,X2,Y2` 欄（プレビュー上でのドラッグ選択と座標入力欄が双方向連動）、Dry-run・Overwrite、Start。右に進捗・Cancel と切り抜き結果表。

---

### System Operations — Storage

#### `folder-size`

フォルダ直下のファイルとサブフォルダを論理サイズ（1024 刻み：`B`, `KB`, `MB`, `GB`, `TB`）の大きい順に表示します。

```text
pytoolbox folder-size [FOLDER] [--top N] [--csv]
```

- **省略時**: `FOLDER` 省略でカレントディレクトリ
- **リンクと読取不能**: シンボリックリンク・ジャンクションは追わずサイズ `0` の `link` 行として表示。読めない項目は件数を集計。
- **CSV 出力**: `--csv` 指定時は `--top` に依らず全件を UTF-8 BOM・CRLF 改行・ヘッダ `name,kind,bytes,size` で標準出力へ出力（`sys.stdout.buffer` 経由で直接出力）。
- **ファイル書込なし・終了動作**: `--dry-run`・ロック・履歴は不要。キャンセル時は走査を終えた直下項目のみ表示（途中項目は省く）。
- **GUI 画面**: パスと Start（Start / Enter）。右に `Progress`・Cancel、完了後に合計サイズ・項目数・割合横棒付きテーブル（先頭 1,000 行）と `data:text/csv` ダウンロードボタンを表示（黒画面コンソールは使わない）。

---

### System Operations — Cleanup

#### `cache-clean`

指定したターゲット名のユーザーキャッシュを検査または削除します。ターゲット名なし・未知の名前は走査前に `InputError`。ディレクトリ自体は残し中身を削除します（リンクは追わず、ルートディレクトリ未存在時は成功扱い）。

```text
pytoolbox cache-clean TARGET [TARGET ...] [--dry-run | --apply]
```

- **指定可能ターゲット（`CACHE_TARGETS`）**:
  - `temp`: 最終更新から **24 時間（86,400 秒）以上経過**した項目のみ削除（Windows `%LOCALAPPDATA%\Temp`、その他 `$XDG_CACHE_HOME/tmp` または `~/.cache/tmp`）
  - `pip`: Windows `%LOCALAPPDATA%\pip\Cache` / 他 `$XDG_CACHE_HOME/pip`（`~/.cache/pip`）
  - `uv`: Windows `%LOCALAPPDATA%\uv\cache` / 他 `$XDG_CACHE_HOME/uv`（`~/.cache/uv`）
  - `npm`: Windows `%LOCALAPPDATA%\npm-cache` / 他 `~/.npm`（`LOCALAPPDATA` 未設定時）
  - `nuget`: `%USERPROFILE%\.nuget\packages` / `~/...`
  - `cargo`: `%USERPROFILE%\.cargo\registry` / `~/...`
- **モード**: 既定は `--dry-run`。`--apply` 時のみ削除し、対象ルートへ `target_lock` を掛け、実行履歴へ記録。使用中ファイルは失敗件数へ計上して続行。
- **GUI 画面**: 6 ターゲットのチェックボックス、Apply チェックボックス（既定 OFF）、Start。右に `Progress`・Cancel、予定または削除ファイル・合計バイト数・失敗件数を表示。

---

### System Operations — Network

#### `port`

指定ホストの TCP ポートへ接続を試み、開閉と応答時間（ms）を計測します。

```text
pytoolbox port HOST PORT [--timeout SEC]
```

- **制約**: 空白ホスト・ポート `1〜65535` 範囲外・非正または有限でないタイムアウト（既定 `3` 秒）は `InputError`。閉鎖・タイムアウトは `closed` を表示して終了 `0`。
- **GUI 画面**: 即時応答フォーム（ホスト名、ポート `1〜65535`、タイムアウト既定 `3`、Start）。

#### `ping`

Subprocess Runner 経由で ICMP Echo を 1 回送信します（Windows: `ping -n 1 -w <ms> HOST`、その他: `ping -c 1 -W <ceil(sec)> HOST`）。

```text
pytoolbox ping HOST [--timeout SEC]
```

- **制約・終了コード**: 空白ホスト・不正タイムアウト（既定 `3` 秒）は `InputError`（`1`）。到達不可は診断結果として終了 `0`。外部コマンド起動失敗は `OperationError`（`2`）。
- **GUI 画面**: 即時応答フォーム（ホスト名、タイムアウト既定 `3`、Start）。

#### `udp`

`0.0.0.0` で UDP データグラムを待ち受け、受信ごとにログを出力します。

```text
pytoolbox udp [--port PORT]
```

- **仕様**: 既定ポート `9000`（範囲外は `InputError`、ポート使用中などの開始失敗は `OperationError`）。受信バッファ 65,535 バイト。1 件 1 行で地域時刻（ミリ秒付き）、送信元 IP:Port、バイト長、16 進数、UTF-8 文字列（非 UTF-8 は `not UTF-8`）を出力。16 進・文字列は先頭 1,024 バイトまで出力し超過時は `(+N bytes)` を付与。
- **GUI 画面**: 黒画面コンソールを使う流す画面。Start で開始（前回の受信が動いていれば停止して差し替え）、待機中は `Listening on 0.0.0.0:<port>`・Stop・最新 1,000 行のログを表示し、停止後に受信件数を表示。

---

## 4. Web GUI (`pytoolbox-gui`) HTTP・画面仕様

- **テーマ・UI**: OS の明暗設定に `light-dark()` で自動追従。アイコンは [Fluent UI System Icons](https://github.com/microsoft/fluentui-system-icons)（MIT、size 20、`regular`）を `static/icons/` に同梱し CSS `mask`＋`currentColor` で描画。ショートカットキーやモジュール単位のオン／オフ無効化機能は持たない。
- **サイドバーとホーム**: 検索欄・ホーム（すべてのツール）・系統別カテゴリ木（登録済みツールがある分類のみ表示）・下端 `Logs & History` へ並ぶ。サイドバーは左上のボタンでアイコン幅へ折りたたみ可。ホーム上部には実行履歴から重複なし直近最大 6 件の「最近使ったツール」カードを表示（履歴 0 件時は非表示）。
- **セキュリティ境界**:
  - `GET` でツールを実行しない（すべて `POST`）。
  - すべてのリクエストの `Host` が `127.0.0.1:<port>` または `localhost:<port>` でなければ `403`（DNS リバインディング対策）。
  - すべての `POST` の `Origin` が `http://127.0.0.1:<port>` または `http://localhost:<port>` でなければ `403`（CSRF 対策）。CORS ヘッダは返さない。
  - フロントエンド資産（`htmx-2.0.11.min.js`, `htmx-ext-sse-2.2.4.min.js`, `app.css`, アイコン）はすべて静的同梱し、CDN は使わない。
- **HTTP エンドポイント**:
  - `GET /`: シェルページ（サイドバー＋ホームカード）
  - `GET /search?q=<text>`: ツール名・要約の部分一致検索（大小無視、`200ms` デバウンス付き）。`HX-Request` でペイン断片を返す。
  - `GET /history`（`?tool=<tool>`）: 実行履歴の最新 200 件一覧（日時、ツール名、状態、件数、要約、`<details>` 内の引数 JSON・エラー全文。Clear ボタンなし）。
  - `GET /tools/<tool>`: ツール画面（未知ツールは `404`）。
  - `POST /tools/<tool>`: 即時実行ツールのフォーム送信（結果 HTML 断片、成功時は `#error` を `hx-swap-oob="true"` でクリア、`InputError` は `422`、`OperationError` や予期しないエラーは `500`）。
  - `POST /tools/<tool>/runs`: ストリーミング実行開始（`uuid4` hex の実行 ID 断片を返す。フォーム送信値は `snake_case`）。
  - `GET /runs/<id>/events`: SSE ストリーム（`Cache-Control: no-cache`。イベント名: `progress`, `log`, `result`, `error`, 終了時 `done`）。
  - `POST /runs/<id>/cancel`: 実行中ジョブをキャンセルし `204` を返す。
  - `GET /static/<file>`: 同梱アセット配信。
