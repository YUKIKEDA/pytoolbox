# チュートリアル

このチュートリアルでは、`pytoolbox` と Web GUI（`pytoolbox-gui`）を導入し、開発ユーティリティ（CLI）、ファイルを安全に書き出す操作（`--dry-run` と上書き保護）、そしてブラウザ上の Web GUI を一通り動かして流れを掴みます。

詳しい目的別の手順は [ハウツーガイド](how-to.md)、全コマンドと画面の仕様一覧は [リファレンス](reference.md)、設計の考え方は [解説](explanation.md) を参照してください。

---

## 1. 導入する

このプロジェクトは `uv` と Python 3.12 を前提としています。用途に合わせて次のいずれかで導入してください。

### CLI と Web GUI の両方を入れる場合

```powershell
uv tool install ./packages/pytoolbox --with ./packages/pytoolbox-gui
```

> `uv tool install` は主対象に指定したパッケージのコマンドだけを公開します。`pytoolbox` コマンドを有効にするため、`pytoolbox` を先に指定して `--with` で `pytoolbox-gui` を追加します。

### CLI だけを入れる場合

```powershell
uv tool install ./packages/pytoolbox
```

### リポジトリのローカル開発環境からそのまま試す場合

クローンしたディレクトリで依存関係を同期し、`uv run` 経由で実行します。

```powershell
uv sync --all-packages --group dev
uv run pytoolbox list
```

> **補足**: この先の手順でインストール済みの `pytoolbox` を使う場合は `pytoolbox ...`、ローカル開発環境から実行する場合は先頭に `uv run` を付けて `uv run pytoolbox ...` と実行してください。

---

## 2. 使えるツールを確認する

最初に `pytoolbox list` を実行して、登録されているシステム（`Developer Utilities` / `System Operations`）、カテゴリ、およびコマンド名を確認します。

```powershell
pytoolbox list
```

このツールボックスのコマンドはすべて `pytoolbox <ツール名>` の 1 段で実行できます。

---

## 3. 開発ユーティリティを試す（文字数・正規表現）

まずはファイルやシステムを変更しない読み取り・変換系のツールを動かします。

### 文字数を数える（`text-count`）

文字列を直接渡すか、標準入力からパイプで渡します。

```powershell
pytoolbox text-count "Hello, 世界"
```

出力には総文字数（`total`）、空白を除いた文字数（`non-space`）、半角文字数（`half-width`）が並びます。

PowerShell やシェルのパイプ経由でも渡せます。

```powershell
"Hello, 世界" | pytoolbox text-count
```

### 正規表現を照合する（`regex`）

全一致（`-g`）オプション付きでパターンを照合してみます。

```powershell
pytoolbox regex "(?P<num>\d+)" "order-12, box-345" -g
```

一致した箇所の番号・位置範囲・文字列と、名前付き／番号付きグループの内訳が出力されます。

---

## 4. ファイルを書き出すツールを安全に試す（`csv-gen`）

書く・消す操作を含むツールには **Dry-run プレビュー** と **上書き保護** が備わっています。ここでは一時フォルダ（例としてカレントディレクトリ下の `tutorial_work`）を使って挙動を確認します。

### ステップ 1: `--dry-run` で予定だけ確かめる

```powershell
pytoolbox csv-gen ./tutorial_work/users.csv --rows 5 --col id:number --col name:text --col active:boolean --seed 42 --dry-run
```

`dry-run:` で始まる予定出力パスと行数が表示され、実際のファイルや親ディレクトリはまだ作られません。実行履歴（`Logs & History`）にも残りません。

### ステップ 2: 実際にファイルを書き出す

`--dry-run` を外して実行します。

```powershell
pytoolbox csv-gen ./tutorial_work/users.csv --rows 5 --col id:number --col name:text --col active:boolean --seed 42
```

Excel でそのまま開ける UTF-8（BOM 付き）の CSV ファイルが生成されます。

### ステップ 3: 上書き保護を確認する

そのままもう一度同じコマンドを実行してみてください。

```powershell
pytoolbox csv-gen ./tutorial_work/users.csv --rows 5 --col id:number --col name:text --seed 42
```

既存ファイルがあるため失敗（終了コード `1`、標準エラー出力に `error: ...`）になり、ファイルは保護されます。上書きしたい場合だけ `--overwrite` を明示します。

---

## 5. Web GUI を起動して操作する

CLI と同じツール群をブラウザから操作できます。

```powershell
pytoolbox gui
```

ローカル Web サーバーが `http://127.0.0.1:8000` で起動し、既定のブラウザで開きます。

1. **ツールを探す**: 左サイドバーの検索ボックスに `regex` や `copy` と入力すると、名前と要約で即座に絞り込まれます。左上のボタンでサイドバーをアイコンのみに折りたたむこともできます。
2. **開発ユーティリティを使う**: **Text → `regex`** を開き、パターンや対象文字列を入力すると、ボタンを押さなくても入力のたびに右側のプレビューとグループ一覧が更新されます。
3. **進捗付きのツールを動かす**: **Generators → `mock-image`** や **System Operations → `folder-size`** を開き、パラメータを入れて **Start** を押すと、リアルタイムに進み具合が表示され、必要なら **Cancel** で中断できます。
4. **実行履歴を確認する**: ステップ 4 で実行した `csv-gen` や GUI 上の書き込み実行は、左下の **Logs & History** に記録されています（`--dry-run` 実行は記録されません）。ホーム画面上部にも「最近使ったツール」として最大 6 件まで表示されます。

確認が終わったら、確認に使った `./tutorial_work` ディレクトリを削除し、Web GUI を起動したターミナルで `Ctrl+C` を押して終了します。
