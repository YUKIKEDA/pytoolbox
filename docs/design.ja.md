# `pytoolbox` & `pytoolbox-gui` プロジェクト設計・仕様書 (改訂版)

## 1. プロジェクト概要

### 1.1 コンセプト
エンジニアの日常を支える **「PC運用管理」** と **「開発ユーティリティ（DevToys互換）」** を1つの基盤に集約したスイスアーミーナイフ型ツールボックス。
コアエンジン `pytoolbox`（CLI・Pythonライブラリ）と、Web GUIラッパー `pytoolbox-gui`（ブラウザUI）で構成する。

### 1.2 実装言語の方針
* **原則 Python 優先**:
  移植性、テストのしやすさ、型安全性を考慮し、機能は原則Pythonで実装する。
* **ネイティブシェル（Bash / PowerShell）の限定利用**:
  OS固有の低レイヤー操作、複雑なパイプライン処理、環境変数の一括適用など、Pythonでは実装が煩雑になる領域にのみシェルスクリプトを併用する。

---

## 2. 全体アーキテクチャ

ツールは性質に応じて **「即時処理（DevTools系）」** と **「プロセス実行（SystemTools系）」** の2つの実行パスを持つ。

```text
[ ユーザー操作 ]
   │
   ├──▶ ① CLI直接実行 : pytoolbox <tool>
   │
   └──▶ ② Web ブラウザ : pytoolbox-gui (DevToys風UI)
              │
              ▼ (pytoolbox コア)
       ┌────────────────────────────────────────────────────────┐
       │ 【パス A】Developer Tools (DevToys系)                  │
       │  ・YAML整形, ハッシュ計算, 文字数, 正規表現等            │
       │  ・Pythonインプロセスで超高速実行（入力即レスポンス）    │
       └────────────────────────────────────────────────────────┘
       ┌────────────────────────────────────────────────────────┐
       │ 【パス B】System Tools (管理・運用系)                   │
       │  ・キャッシュ掃除, 環境構築, ポート調査, ネットワーク修復 │
       │  ・Subprocess実行 (外部コマンド / Shell / PowerShell)  │
       │  ・SSEリアルタイムログ配信 ＋ 排他ロック ＋ 履歴DB     │
       └────────────────────────────────────────────────────────┘
```

2系統は、実行のしかたと GUI の分類である。CLI のコマンドは分けない。

* **書く・消すツール**: `--dry-run`、書き先ごとの排他ロック、実行履歴を持つ。読むだけのツールは持たない。
* **ログ配信**: 一括処理の進捗と UDP 受信のログだけを流す。GUI は SSE、CLI は標準出力。
* **Subprocess**: 外部コマンドとシェルスクリプトだけ。Python で書くツールは同じプロセスで動かし、`cancel` で止める。
  * 例外は正規表現の照合である。CLI も GUI も別プロセスの作業役で照合する。`re` の照合は C の中で回り続けて `cancel` も Ctrl+C も確かめる機会が無く、止まらない照合のスレッドは外から止められない。GUI は 1 秒で打ち切る。画面は打つたびに照合するので、打ちかけのパターンが溜まると CPU を使い切るからである。CLI は打ち切らず、Ctrl+C で作業役を止めて終わる。大きな入力への重い照合も CLI の正当な使い方だからである。作業役は Python のプロセスで、外部コマンドを動かす Runner ではない。
* **シェルスクリプト**: ツールの中身である。ツールが Runner を通して呼ぶ。CLI と GUI に、スクリプトを名前やパスで実行する入口は作らない。

---

## 3. ディレクトリ構成 (`uv` Monorepo)

```text
pytoolbox/
├── pyproject.toml                 # ワークスペース定義
├── Justfile                       # 開発タスクランナー
├── README.md
│
├── packages/
│   ├── pytoolbox/                 # 【コア & CLI】
│   │   ├── pyproject.toml
│   │   └── src/pytoolbox/
│   │       ├── cli.py             # CLIエントリポイント
│   │       ├── runner.py          # システムタスク用実行エンジン (Subprocess)
│   │       ├── db.py              # SQLite履歴保存
│   │       └── tools/             # 組み込みツール群
│   │           ├── dev/           # 【DevTools系】Pythonインプロセス関数
│   │           │   ├── encoders.py (URL, JWT)
│   │           │   ├── formatters.py (YAML, SQL)
│   │           │   └── generators.py (パスワード, Hash)
│   │           └── system/        # 【SystemTools系】Python実装の管理タスク
│   │               ├── cleanup.py
│   │               └── sysinfo.py
│   │
│   └── pytoolbox-gui/             # 【Web GUI】
│       ├── pyproject.toml
│       └── src/pytoolbox_gui/
│           ├── cli.py             # `pytoolbox gui` から呼ぶ起動部 (uvicorn)
│           ├── server.py          # FastAPI / APIルーティング
│           ├── sse.py             # リアルタイムログ配信
│           ├── static/            # htmx と CSS を同梱する (CDN は使わない)
│           └── templates/         # HTML (Jinja2 + HTMX + CSS)
│
└── native-scripts/                # 【ネイティブシェル専用領域】
    ├── mac/                       # macOS専用 (.sh, AppleScript)
    ├── win/                       # Windows専用 (.ps1)
    └── common/                    # 共通 (.sh)
```

---

## 4. 2系統のツール設計規約

### 4.1 Developer Tools (DevToys系)
* **実装方式**: 純粋なPython関数（引数を受け取って加工文字列を返す純粋関数）。
* **UI表現**: 左右（または上下）分割のテキストエリア。「左に入力した瞬間に右へ整形・変換結果が出る」DevToys仕様。
* **実装例**:
  ```python
  # packages/pytoolbox/src/pytoolbox/tools/dev/text.py
  def count_text(text: str) -> TextCount:
      """文字数を数える"""
      return TextCount(
          total=len(text),
          non_space=sum(not char.isspace() for char in text),
          half_width=sum(unicodedata.east_asian_width(char) in _HALF_WIDTH for char in text),
      )
  ```

### 4.2 System Tools (PC管理系)
* **実装方式**: Pythonスクリプト、または `native-scripts/` 配下のシェルスクリプト。
* **安全基準**:
  * 破壊的操作を含む場合は `--dry-run` オプションを必須とする。
  * コマンドインジェクション防止のため `shell=True` は使用禁止。
  * 書く・消すツールは、書き先ごとに排他ロックを取る。同じ書き先への2つ目の実行は `OperationError` で止める。
  * 書く・消すツールの実行は履歴に残す。`--dry-run` の実行は残さない。
* **UI表現**: 引数入力フォーム（オプション指定） ＋ 実行ボタン ＋ リアルタイム黒画面コンソール（SSE）。

---

## 5. Web GUI (`pytoolbox-gui`) の設計

DevToysライクなレイアウトを採用し、左サイドバーで全ツールをシームレスに切り替えます。

* **テーマ**: OS の明暗設定に従う。暗い設定なら DevToys や PowerToys と同じ暗い画面になる。
* **サイドバー**: 上から検索欄、「すべてのツール」（ホーム）、系統と分類の木、下端の履歴への入口の順に並ぶ。系統と分類は開け閉めでき、ツールはアイコンと名前で並ぶ。左上のボタンでサイドバーをアイコンだけの細さに畳める。ツールの画面は入力と出力の左右分割なので、畳むと広く使える。
* **検索**: 入力のたびに、ツールの名前と要約で絞った一覧をサーバーが返す。ツールは約 25 あるので、名前で探せる必要がある。
* **ホーム**: 系統ごとに見出しを付け、ツールのカード（アイコン、名前、要約）を並べる。上に最近使ったツールを並べる。
* **アイコン**: Fluent UI System Icons（MIT）を使う。DevToys と PowerToys と同じ系統の絵柄である。
* **正規表現の画面**: 左にパターンの 1 行入力、`g` `i` `m` のチェックボックス、対象の文字列を置き、どれかを変えるたびに照合する。右上に一致した箇所を色付けした文字列、右下に一致の一覧（何番目か、開始と終了の位置、一致した文字列、番号付きと名前付きのグループ）を出す。正規表現を試す目的は、どこに当たったかと、グループに何が取れたかを確かめることである。
* PowerToys のモジュールごとのオン・オフとショートカットキーは持たない。ツールに有効・無効は無く、常駐もしないので、ショートカットキーは効かない。

### 5.1 サイドバーのカテゴリ構造
```text
▼ Developer Utilities
  ├─ Encoders / Decoders (URL, JWT)
  ├─ Formatters (YAML)
  ├─ Generators (Hash, Password, CSV 生成, JSON 生成, モック画像)
  ├─ Text (Diff, 正規表現テスター, 文字数)
  └─ Code (リソース比較, ハードコーディング検出)

▼ System Operations
  ├─ Files (ファイルコピー, フォルダコピー, PDF 分割, ファイル結合, 画像クリップ)
  ├─ Storage (フォルダサイズ)
  ├─ Cleanup (キャッシュ削除, 一時ファイル掃除)
  ├─ Network (ポート調査, Wi-Fiリセット, 疎通確認, UDP 受信)
  └─ Setup (開発環境キッティング, パッケージ更新)

▼ Logs & History
  └─ 過去の実行履歴 (SQLite)
```

分け方は、利用者が扱う対象である。テキストとコードは Developer Utilities、PC のファイル、ストレージ、ネットワークは System Operations に置く。安全策は分類ではなく、ツールごとに付く。`pytoolbox list` も同じ系統と分類を示す。

上の木は、最終的な置き場所である。サイドバーは登録簿から描き、登録済みのツールがある分類だけを出す。押して動かない項目は出さない。

「Logs & History」は系統ではない。サイドバーの下端に固定する入口で、ホームの最近使ったツールと一緒に、実行履歴が入るときに出す。

### 5.2 処理フローの違い（FastAPI実装）

サーバーは HTML を返す。HTMX が受けて画面の一部を差し替える。JSON を返すデータ API は持たない。プログラムから使う口は CLI とライブラリである。パスはツール名で揃え、系統では分けない。流すかどうかは系統ではなく、ツールの性質で決まるからである。

* **すぐ返すツール**:
  `POST /tools/regex` ──▶ 結果の HTML 断片を返す。
* **流すツール（一括処理、UDP 受信）**:
  `POST /tools/cache-clean/runs` ──▶ 実行を始め、実行 ID を埋めた HTML 断片を返す。
  `GET /runs/<id>/events` ──▶ **Server-Sent Events (SSE)** で進捗とログをリアルタイム配信。
  `POST /runs/<id>/cancel` ──▶ 止める。
* **検索**:
  `GET /search?q=…` ──▶ 名前と要約で絞ったツールの一覧の HTML 断片を返す。ツールは走らせない。
* ツールを走らせるのは POST だけである。GET はブラウザの先読みやリンクでも走るので、画面と SSE の受信にだけ使う。
* 書く・消すツールを持つローカルのサーバーなので、他のサイトからの要求と DNS リバインディングを拒む。細目は `docs/conventions.md`。

---

## 6. CLI インターフェース設計 (`pytoolbox`)

CLIからもすべての機能に直感的にアクセスできます。コマンドは `pytoolbox <tool>` の一段で、系統は `pytoolbox list` が示す。

```bash
# 1. 一覧表示
pytoolbox list

# 2. System系ツールの実行 (ログを標準出力に流す)
pytoolbox cache-clean temp --dry-run

# 3. Dev系ユーティリティの利用 (パイプ連携対応)
cat notes.txt | pytoolbox text-count
pytoolbox regex "\d+" "a1b22" -g

# 4. GUIの起動
pytoolbox gui   # GUI サーバーを立ち上げてブラウザを開く。pytoolbox-gui が無ければ入れ方を示す
```

---

## 7. 配布・導入手順

### 7.1 基本セットアップ (`uv` 利用)
```bash
# CLIのみ使用する場合
uv tool install ./packages/pytoolbox

# Web GUIを含むフル機能を使用する場合
uv tool install ./packages/pytoolbox --with ./packages/pytoolbox-gui
```

`uv tool install` は、名指ししたパッケージのコマンドだけを出す。`pytoolbox-gui` を名指しすると `pytoolbox` が出ないので、`pytoolbox` を名指しし、GUI を同じ環境に入れる。

### 7.2 起動
* **CLI**: そのまま `pytoolbox` コマンドを使用。
* **GUI**: ターミナルで `pytoolbox gui` と打つと、ローカルWebサーバー（`127.0.0.1:8000`、uvicorn）が立ち上がり、ブラウザが自動的に開く。起動のコマンドはこれ一つである。

---

## 8. 実装ロードマップ

各段で、ツールは CLI と GUI を同じ行で出す。行は `docs/roadmap.md`。

* [ ] **段 1: 土台**
  * 例外と結果の型、`pytoolbox list`、GUI の骨組み（FastAPI、サイドバー、SSE）。
* [ ] **段 2: 読むだけのツール**
  * 文字数、正規表現、リソース比較、ハードコーディング検出、フォルダサイズ、UDP 受信。
* [ ] **段 3: 安全策**
  * 書き先ごとの排他ロック、SQLite による実行履歴と、その画面（Logs & History）。
* [ ] **段 4: 書く・消すツール**
  * CSV 生成、JSON 生成、モック画像、ファイルコピー、フォルダコピー、PDF 分割、ファイル結合、画像クリップ、キャッシュ掃除。
* [ ] **段 5: システム実行エンジン (Runner)**
  * Subprocess の実行、引数の配列渡しバリデーション。ツールの中身としてのシェルスクリプトの実行。疎通確認。

スクリプトの自動検出と、PC ログイン時の常駐は作らない。スクリプトはツールの中身であり、利用者に見せないので、検出して並べる先が無い。常駐は要らない。

JSON 整形、Base64、UUID 生成は作らない。必要ないからである。

---

## 9. 初期ツール一覧

初期で作るツールの一覧である。出典は手元の6つ。PyDevToys が eel で大半を既にまとめており、FolderSizer だけがそこへ入っていない。設定画面はチェックボックスだけで動作が無いので、一覧に入れない。

出典の違いは「決定」の列のとおり決めた。型、エラー、ファイル、テスト、終了コードの決めは `docs/conventions.md` にある。ライセンスは MIT（`LICENSE`）。

| ツール | すること | 出典 | 決定 |
| --- | --- | --- | --- |
| 文字数 | 総文字数、空白を除いた数、半角の数を数える | PyDevToys の画面だけ。Python コアは無い | 半角は East Asian Width の H と Na。総数はコードポイント数。空白は `str.isspace` |
| 正規表現 | パターンと文字列を照合する。フラグは g / i / m | PyDevToys の画面だけ。ブラウザ内で実行している | `i` は `IGNORECASE`、`m` は `MULTILINE`、`g` は全一致。`g` が無ければ最初の一致。フラグはこの 3 つだけ。照合は Python の `re` で、CLI と GUI の結果は一致する。照合は CLI も GUI も別プロセスで行う（§2）。GUI は 1 秒で打ち切る。CLI は打ち切らず、Ctrl+C で止める |
| リソース比較 | 2つの `.resx` の `data` 名を比べ、一致・不一致・片方のみを返す | PyDevToys | 無し |
| ハードコーディング検出 | ディレクトリを拡張子で歩き、文字列、4桁以上の数値、IP、URL、メールを行番号付きで返す。コメント行は飛ばす | PyDevToys | 無し |
| 疎通確認 | `ping` を1回送る。TCP ポートへ接続し、開閉と応答時間を返す | PyDevToys。引数は配列渡し | 無し |
| フォルダサイズ | 直下のファイルとフォルダを大きい順に出す。フォルダは中身の合計。リンクとジャンクションは追わない。読めない項目は件数だけ報告する。`--top` は表示件数、CSV は全件、UTF-8 BOM | FolderSizer。PyDevToys には無い | 無し |
| CSV 生成 | 列の型（text, number, boolean, date）と行数から CSV を書く | PyDevToys | 種は任意。Excel 向けなので BOM 付き |
| JSON 生成 | プロパティ定義と件数から JSON を書く。型は string, number, boolean, date, email, uuid, url, ip, path, array, object, date_range, custom_list | PyDevToys | 種は任意 |
| モック画像 | 幅、高さ、枚数、出力先から、寸法を描いた画像を書く。比率をランダムにする指定がある | PyDevToys | 種は任意 |
| ファイルコピー | 同じ内容を、書式で連番にした名前で複数書く | PyDevToys | 無し |
| フォルダコピー | フォルダを、名前と番号の書式で複数コピーする | PyDevToys | 無し |
| PDF 分割 | PDF を1ページずつ `page_N.pdf` として書く | PDFDivider と PyDevToys | 無し |
| ファイル結合 | 選んだテキストを、区切り付きで1つの `.txt` に書く。読めないファイルはその旨を書く | FileMergerGUI と PyDevToys | 名前は `merged_YYYYMMDD_HHMMSS.txt` |
| 画像クリップ | プレビューで範囲を選び、フォルダ内の jpg, jpeg, png, bmp, gif を、その矩形で切り、`cropped_` を付けて書く。元フォルダへ書く選択がある。JPEG 品質 95 | PyImageCripper と PyDevToys | 矩形は画像のピクセル座標。画像の外は丸める |
| キャッシュ掃除 | 引数で指定した名前のキャッシュだけを消し、ストレージを空ける | 手元の6ツールには無い | 名前は `temp`、`pip`、`uv`、`npm`、`nuget`、`cargo`。名前が無い、または未知の名前は `InputError` で、何も走査しない。既定と `--dry-run` は消す予定のパスとバイト数を出す。`--apply` のときだけ消す。`temp` は `%LOCALAPPDATA%\Temp` で、最終更新から 24 時間未満は残す。使用中で消せないファイルは失敗として数え、残りは続ける。`pip` は `%LOCALAPPDATA%\pip\Cache`、`uv` は `%LOCALAPPDATA%\uv\cache`、`npm` は `%LOCALAPPDATA%\npm-cache`、`nuget` は `%USERPROFILE%\.nuget\packages`、`cargo` は `%USERPROFILE%\.cargo\registry`。これらは中身を全部消す。ディレクトリ自体は残す。リンクは追わない。ルートが無いときは成功。GUI も同じ契約 |
| UDP 受信 | `0.0.0.0` で UDP を待ち、届いたデータグラムをログする。開始と停止がある | UDPListener と PyDevToys | ポートの既定は 9000 で、指定で変えられる。長さ、16進、UTF-8 で読めた文字列をログする |
