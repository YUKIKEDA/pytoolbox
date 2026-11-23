# ハウツーガイド

日常的な運用管理や開発作業の目的に合わせた具体的な使い方をまとめています。引数・オプションの網羅的な仕様は [リファレンス](reference.md) を参照してください。

---

## 1. テスト・検証用のダミーデータを生成する

### CSV ファイルを作る（`csv-gen`）

列の型（`text`, `number`, `boolean`, `date`）と行数、`--seed` を指定して再現性のある CSV（UTF-8 BOM 付き）を出力します。

```powershell
pytoolbox csv-gen ./users.csv --rows 100 --col name:text --col age:number --col active:boolean --col joined:date --seed 123
```

### JSON 配列ファイルを作る（`json-gen`）

単純なフィールド定義は `--col`、ネストや範囲指定を含む構成は `--schema` に JSON 文字列または JSON スキーマファイルのパスを渡します（UTF-8 BOM なし）。

```powershell
# スキーマファイルを使う場合
pytoolbox json-gen ./records.json --rows 50 --schema ./schema.json --seed 123

# --col で手軽に指定する場合
pytoolbox json-gen ./items.json --rows 20 --col id:uuid --col email:email --col score:number
```

### モック画像を連番で生成する（`mock-image`）

画像サイズ（`--width`, `--height`）と枚数（`--count`）を指定して、寸法が描画された PNG または JPEG 画像を生成します。比率をランダムに変化させたい場合は `--random-ratio` を付けます。

```powershell
pytoolbox mock-image ./mock_images --width 800 --height 600 --count 5 --random-ratio --format jpg --seed 7
```

---

## 2. ファイル・フォルダを連番で複製する

### 1 つのファイルを連番コピーする（`file-copy`）

`--format` で `{stem}`（拡張子なしファイル名）、`{suffix}`（拡張子）、`{n}`（番号）を使えます。先に `--dry-run` で出力先を確認してから実行すると安全です。

```powershell
pytoolbox file-copy ./template.txt ./copies --count 5 --format "{stem}_{n:03d}{suffix}" --start 1 --dry-run
pytoolbox file-copy ./template.txt ./copies --count 5 --format "{stem}_{n:03d}{suffix}" --start 1
```

### フォルダごと連番コピーする（`folder-copy`）

ディレクトリの中身を再帰的に連番フォルダへ複製します。

```powershell
pytoolbox folder-copy ./project_skel ./sandboxes --count 3 --format "{name}_{n:03d}" --start 10
```

---

## 3. PDF・テキスト・画像をまとめて加工する

### PDF を 1 ページずつ別ファイルに分割する（`pdf-split`）

```powershell
pytoolbox pdf-split ./manual.pdf ./pages --format "{stem}_p{n}.pdf"
```

### 複数のテキストファイルを 1 つに結合する（`file-merge`）

指定順にテキストを結合し、出力フォルダへ `merged_YYYYMMDD_HHMMSS.txt`（UTF-8 BOM なし）を書き出します。区切り線は `--sep`（既定 `--- {name} ---`）で調整できます。途中で読めない入力ファイルがあっても、出力本文内にエラー理由を記した注記を差し込みつつ `ItemFailure` として報告し、残りのファイルの結合を続けます。

```powershell
pytoolbox file-merge ./part1.txt ./part2.txt ./part3.txt --output-dir ./dist --sep "=== {name} ==="
```

### フォルダ内の画像を同じ矩形で切り抜く（`image-clip`）

矩形の pixel 座標（`x1,y1,x2,y2`）を指定します。出力先 `--output-dir` を省略すると元のフォルダ内に `cropped_` プレフィックス付きで保存されます。座標が目視しづらい場合は Web GUI（`image-clip` 画面）を使うと、先頭画像のプレビュー上でマウスドラッグして切り抜き範囲をそのまま指定できます。

```powershell
# 別フォルダへ切り抜き保存する
pytoolbox image-clip ./screenshots --box 100,80,900,600 --output-dir ./cropped

# 元フォルダへ cropped_ 付きで書き出し（既存ファイルを上書きする場合）
pytoolbox image-clip ./screenshots --box 100,80,900,600 --overwrite
```

---

## 4. ソースコードとリソースファイルを検査する

### 2 つの `.resx` リソースファイルの差分を比べる（`resx-compare`）

翻訳漏れ（片方にしか無いキー）や値の違い（`differ`）だけを一覧します。一致したキーも含めて全件表示したい場合は `--all` を付けます。Web GUI ではファイル 1・ファイル 2 を入力後、`Enter` またはフォーカス移動で比較が走ります（入力中の途中パスではエラーにしません）。

```powershell
pytoolbox resx-compare ./Strings.ja.resx ./Strings.en.resx
pytoolbox resx-compare ./Strings.ja.resx ./Strings.en.resx --all
```

### ハードコーディングされた文字列・数値・URL 等を検出する（`hardcoding`）

対象の拡張子（`--ext`）、スキップするフォルダ名（`--exclude`）、検出種別（`--kind`: `string`, `number`, `ip`, `url`, `email`）を指定できます。各オプションはカンマ区切りまたは複数回指定できます。

```powershell
pytoolbox hardcoding ./src --ext .py,.js --exclude .venv,node_modules,dist --kind url,ip,email
```

Web GUI 上の結果表は先頭 1,000 件までに制限されるため、大規模プロジェクトで全件を確認したい場合は CLI から実行して標準出力を参照してください。

---

## 5. ディスク容量の調査とキャッシュ掃除を行う

### フォルダ直下のサイズを大きい順に出す（`folder-size`）

引数を省略するとカレントディレクトリを対象にします。上位件数に絞るには `--top N`、表の代わりに全件を CSV（UTF-8 BOM 付き）で保存するには `--csv` を使います。Web GUI では結果画面からそのまま CSV をダウンロードできます。

```powershell
pytoolbox folder-size C:\Work --top 20
pytoolbox folder-size C:\Work --csv > folder_sizes.csv
```

### 開発ツールのキャッシュを安全に掃除する（`cache-clean`）

対象は `temp`, `pip`, `uv`, `npm`, `nuget`, `cargo` から 1 つ以上を指定します。`--apply` を付けない限り削除されません（既定は `--dry-run`）。

```powershell
# 1. まず削除予定のファイルと合計サイズを確認する（既定は dry-run）
pytoolbox cache-clean pip uv npm

# 2. 問題なければ --apply で中身を削除する（ルートディレクトリ自体は残る）
pytoolbox cache-clean pip uv npm --apply
```

> `temp` を指定した場合、最終更新から 24 時間未満のファイルは削除されず保持されます。また使用中で削除できないファイルは失敗件数に計上して残りの処理を続行します。

---

## 6. ネットワーク疎通と UDP パケットを診断する

### ホストの到達確認（`ping`）と TCP ポート確認（`port`）

到達不可やポート閉鎖は診断結果として終了コード `0` で報告します。タイムアウト（既定 3 秒）は `--timeout` で調整します。

```powershell
pytoolbox ping example.com --timeout 2
pytoolbox port 127.0.0.1 8000 --timeout 1.5
```

### UDP データグラムを受信してログする（`udp`）

既定ポートは `9000` です。停止するには CLI では `Ctrl+C`、Web GUI では **Stop** ボタンを押します。全件を保存したい場合は CLI の標準出力をファイルへリダイレクトしてください（Web GUI の黒画面コンソールはブラウザ負荷防止のため最新 1,000 行のみを保持します）。

```powershell
pytoolbox udp --port 9500 > udp_capture.log
```

---

## 7. 実行履歴と失敗の詳細を確認する

書く・消す操作（非 `--dry-run` の生成／コピー／分割／結合／切り抜き、および `--apply` 付きのキャッシュ削除）は自動的に SQLite の履歴データベースへ保存されます。

1. `pytoolbox gui` で Web GUI を開きます。
2. サイドバー下端の **Logs & History** を開くと、新しい順に直近 200 件の実行記録が表示されます。
3. ツール名での絞り込みや、各行内の折りたたみ（`<details>`）から「実行時の引数 JSON」や「エラー全文」を展開して原因を追跡できます。
