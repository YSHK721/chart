# codescan — コード重複を 1 行単位で確認する

`python -m tools.codescan` は、リポジトリのソースを走査して **1 行 = 1 レコード**の台帳
（CSV）と、重複クラスタ・依存関係・シンボル種別をまとめた報告（JSON）を出す。
標準ライブラリのみで動く（追加ライブラリなし）。

## 何が出るか

| 出力 | 内容 |
|---|---|
| `.codescan/rows.csv` | 全行の台帳。原子ステップ = 1 行 |
| `.codescan/report.json` | 重複クラスタ・依存グラフ・種別内訳・解析の限界 |
| 標準出力 | 要約（削減見込み行数の大きい順・循環・種別内訳） |

### 行台帳の列

| 列 | 意味 |
|---|---|
| `no` | 出力順の連番（並べ替え後に振る） |
| `dir` | ディレクトリ（絶対パス・末尾 `/`） |
| `file` | ファイル名 |
| `line` | 行番号 |
| `code` | 原文（インデントを含む） |
| `code_key` | 原文を trim ＋ 連続空白畳み込み。**この列でソートすると完全一致の行が隣接する** |
| `code_shape` | 識別子を `ID`・リテラルを `STR`/`NUM` へ畳んだ形。**名前だけ違う複製が隣接する** |
| `line_group` / `line_dup` | 完全一致グループの ID と、その総出現数 |
| `shape_group` / `shape_dup` | 正規化一致グループの ID と、その総出現数 |
| `tok` | その行のトークン数（`}` や `return` のような定型行を弾く閾値に使う） |
| `lang` | `python` / `javascript` |
| `kind` | その行を含む最内シンボルの種別（`function` / `class` / `protocol` / `method` …） |
| `symbol` | その行を含む最内シンボルの修飾名（`Outer.inner`） |
| `dup_id` | 属する重複クラスタ（`F<n>` = 宣言単位 / `B<n>` = ブロック単位） |
| `dup_type` | `type-1`（完全一致）/ `type-2`（識別子・リテラルのみ相違） |
| `dup_unit` | `function` / `block` |
| `dup_count` | そのクラスタの出現箇所数 |
| `dup_partners` | 重複相手の `パス:開始-終了`（最大 3 件＋残数） |
| `imports` | その行が import 文なら指定子 |

## 使い方

```bash
# 既定（全行を code_key 順で出す＝表計算でそのまま重複を追える）
python -m tools.codescan

# 重複行だけを抽出し、重複数の多い順に並べる（1 件ずつ潰す運用）
python -m tools.codescan --only-dup line --min-tok 4 --sort dup

# import 行のような定型を外して見る（既定では何も落とさない）
python -m tools.codescan --only-dup line --min-tok 4 --sort dup --skip-kinds import,comment

# 名前だけ違う複製も含めて抽出
python -m tools.codescan --only-dup shape --min-tok 6 --sort shape

# 範囲を絞る（ディレクトリ指定）
python -m tools.codescan simulator/replay_ui

# 20 件ずつ確認する
python -m tools.codescan --only-dup line --min-tok 4 --sort dup --offset 0 --limit 20 --csv -
```

`--only-dup` の選択肢: `none`（既定）/ `line`（完全一致）/ `shape`（正規化一致）/
`clone`（宣言・ブロッククローンに属する行）/ `any`。

## 重複の 3 つの見方

1. **宣言単位クローン**（`dup_id=F*`）— 関数・メソッド・クラスまるごとの一致。
   単一ソース化の対象がそのまま宣言なので、最も直接に「消せる複製」を指す。
2. **ブロック単位クローン**（`dup_id=B*`）— 宣言をまたぐ／宣言の一部だけの一致。
   手書きのコピペは宣言境界に揃わないことが多く、1 だけでは取り逃す。
3. **同名別実装**（`report.json` の `diverged_names`）— 同じ名前が複数ファイルにあり中身が違う。
   **複製が片方だけ直された**状態を指す。1・2 では検出できない（一致しないため）が、
   複製の最も危険な帰結である。比較は原文で行うため、定数だけが書き換わった差も拾う。

## 走査範囲

`tools/codescan_scope.txt` が唯一源（`+ パターン` / `- パターン`、後勝ち）。
既定でベンダ（`lightweight-charts-python-main/`・保存済み外部ページ `design/`）・
`node_modules`・生成物・`prototype_*` を除く。
CLI の `--include` / `--exclude` は台帳の**後ろに追記**される（台帳を置き換えない）。

### ファイルの同一性は経路ではなく実体

symlink とハードリンクは、同一の実体に至る**別の経路**であって、コードの複製ではない。
経路を単位に数えると 1 つの実装が複数回計上され、重複検出はそれを「複製」と報告する。
同一性は `stat` のデバイス + inode で決め、同一実体を指す経路は 1 本に畳む。畳んだ経路は
`report.json` の `scope.folded_aliases` に `(alias, kept)` で残る（黙って捨てない）。
残す 1 本は symlink でない経路（実体そのもの）を優先する。

この前提が壊れていた間、`simulator/replay_ui/web/js` の 19,570 行のうち 15,528 行は
`indigators/indicator_ui` の実体への symlink の再計上であり、要約の「単一ソース化で消える
見込み行数」を無意味に押し上げていた（ISSUE-304）。

## 依存関係

Python の import 解決根は `tools/dev_paths.txt`（唯一源）から読む。`from . import mod` は
サブモジュール `mod.py` を先に探す（パッケージ `__init__` に化けさせない＝偽の循環を作らない）。
解決できない指定子は外部依存として名前のまま数える。循環は強連結成分で検出する。

## 判定のしきい値

| オプション | 既定 | 意味 |
|---|---|---|
| `--min-tokens` | 40 | 宣言単位クローンの最小トークン数 |
| `--min-lines` | 5 | 宣言単位クローンの最小行数 |
| `--window` | 60 | ブロック単位クローンの種となる連続トークン数 |
| `--max-occurrences` | 40 | 同一窓がこの回数を超えたら種にしない（除外件数は要約に必ず出る） |
| `--fail-over N` | 無効 | 削減見込み行数が N を超えたら終了コード 1（CI 用） |

## 解析の限界（推測で埋めない）

- Python は `ast` + `tokenize` で解析するため、宣言・import は取りこぼさない。
- JavaScript / TypeScript は構文解析器ではなくトークナイザ＋構造走査である。
  取れないものは `report.json` の `limitations` に列挙される（オブジェクトリテラル内の
  メソッド、式中の無名コールバック、文字列でない動的 import など）。
- 解析に失敗したファイルは握り潰さず `parse_errors` に出る。
