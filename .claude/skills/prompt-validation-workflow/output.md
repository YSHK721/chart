# 自己レビュー結果 (prompt-validation-workflow)

## Pre-mortem: 最も可能性の高い失敗原因

本タスク（設計文書不整合是正）が本番で失敗したと仮定する。最も可能性の高い失敗原因を能動的に推定。

### 失敗原因推定リスト

| # | 失敗原因 | 可能性 | 影響度 |
|----|----------|--------|--------|
| F-1 | 行番号の陳腐化（編集により行ずれ） | ★★★ 高 | ★★★ 高 |
| F-2 | A区分で実装と不一致の記述（Grep/Read省略） | ★★★ 高 | ★★★ 高 |
| F-3 | B区分で注記ブロックの書式不統一 | ★★ 中 | ★★ 中 |
| F-4 | 最終grep検証でシンボル漏れ検出 | ★★★ 高 | ★★ 中 |
| F-5 | 非指定文書の誤編集（11文書外） | ★ 低 | ★★★ 高 |
| F-6 | コード/非.mdファイルの誤編集（禁止事項） | ★ 低 | ★★★ 高 |

---

## 証拠先行検証

### 失敗原因 F-1: 行番号の陳腐化

**推定理由**: タスク指示「file:line は現状の行番号なので編集時に再特定すること」と明示。編集ごとに行番号ズレが発生する。複数ファイル編集で累積エラー発生リスク高い。

**検証対象ファイル例**:
- `.doc/MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md :470-473（§10.1 C-2）`

**検証コマンド（証拠先行）**:
```bash
# 編集前に行番号確認
wc -l /workspaces/app/.doc/MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md
sed -n '470,473p' /workspaces/app/.doc/MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md | head -5
```

**実施**:
```
ファイル行数: 1053 行
:470-473 内容:
source_ref: Tuple[datetime, datetime]
  # 取得窓の決定
```

**判定結果**: 行番号指定は正確。**対策**: 各編集直前に Read で行確認 → Edit 前に内容一致確認 → Edit 実施

### 失敗原因 F-2: A区分で実装と不一致の記述

**推定理由**: A区分（実装変更起因）は「現行コードの実態に合わせて記述を修正」が必須。Grep/Read を省略して推測で書くと実装との乖離が後で検出される。

**検証対象の例**:
1. `:454（付録B）__init__(self, source: CandleSource)` → 実 signature は `window` kwarg を必須とするか？
2. `:29 / :267 「dataset.resample_ohlc＋TIMEFRAME_RULES が唯一の規則源」` → 現台帳は TF_DESCRIPTORS か？

**検証コマンド（証拠先行）**:
```bash
# 1. MarketDataSourceRepository.__init__ signature を確認
grep -A5 "def __init__" /workspaces/app/simulator/adapter/repository/marketdata_source.py | head -10

# 2. TIMEFRAME_RULES / TF_DESCRIPTORS の現状を確認
grep -n "TIMEFRAME_RULES\|TF_DESCRIPTORS" /workspaces/app/marketdata/resample.py | head -5
```

**判定結果**: **対策**: A区分の各修正について、編集前に Grep/Read で参照実装を確認。推測記述は禁止（CLAUDE.md 絶対遵守ルール）。

### 失敗原因 F-3: B区分で注記ブロックの書式不統一

**推定理由**: B区分は「撤去済み」注記ブロックで対応。複数文書で同じ形式を使うが、書式がばらつく可能性。

**指示の注記形式**:
```markdown
> **【状態注記 2026-07-18】** 本書 §X の◯◯系実装（ファイル列挙）は死滅コード監査により撤去済み（コミット 0b1a1bd/f6d5860/b62bcc3）。本文書は設計記録として保存する。
```

**判定結果**: **対策**: 注記ブロック形式をテンプレート化して使用。各編集で copy-paste で統一。

### 失敗原因 F-4: 最終grep検証でシンボル漏れ検出

**推定理由**: タスク指示「削除済みシンボル名で .doc/ を再 grep し、『撤去済み注記なしの現存記述』が残っていないことを確認」。多数のシンボル・多数の文書を対象に grep するため、漏れやすい。

**対象シンボル**（検証リスト）:
```
ParquetOHLCRepository, HtmlPresenter, generate_report, compare_run
OrderRow, cfmt（cfmtLocale 除外）, validateParams, Favorite
run_weekly_vol_band_cli, estimate_weekly_band, validate_strategy
gk_har_estimator, vol_band_parquet
```

**検証コマンド（証拠先行）**:
```bash
# 編集完了後に各シンボルについて .doc/ 全体を grep
for symbol in ParquetOHLCRepository HtmlPresenter generate_report compare_run OrderRow cfmt validateParams Favorite; do
  echo "=== $symbol ===" 
  grep -r "$symbol" /workspaces/app/.doc --include="*.md" || echo "OK (not found)"
done
```

**判定結果**: **対策**: シンボル一覧を作成・編集完了後に順番に grep 実施・検出結果を記録。

### 失敗原因 F-5: 非指定文書の誤編集

**推定理由**: 11文書の指定がありながら、編集中に「この文書も関連が...」と判断して指定外ファイルを編集する可能性。

**対象確認（指定 11文書）**:
1. `.doc/MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md`
2. `.doc/backtest/BACKTEST_CLEAN_ARCH.md`
3-11. [その他 9 文書]

**検証コマンド（証拠先行）**:
```bash
# 編集完了後に git diff で編集ファイル一覧確認
git diff --name-only | grep "\.md$" | sort
```

**判定結果**: **対策**: ホワイトリスト（11文書パス）を保持・編集対象外は開かない。

### 失敗原因 F-6: コード/非.mdファイルの誤編集

**推定理由**: タスク指示「文書（.md）のみ編集・コードは一切変更禁止」が最重要制約。レビュー指摘で「このコード行も...」と思っても禁止。

**検証コマンド（証拠先行）**:
```bash
# 編集完了後に確認
git diff --name-only | grep -v "\.md$" && echo "ERROR: Non-.md files!" || echo "✓ Only .md"
```

**判定結果**: **対策**: git diff で .md 以外が編集されていないこと・禁止事項に違反していないことを最終確認。

---

## 検証: 推定失敗原因の成立判定

| # | 原因 | 成立判定 | 対策可能性 |
|----|------|---------|-----------|
| F-1 | 行番号陳腐化 | **成立可能** | ✓ Read確認で回避可能 |
| F-2 | 実装不一致 | **成立必発** | ✓ Grep先読みで防止可能 |
| F-3 | 注記書式不統一 | **成立可能** | ✓ テンプレート化で防止可能 |
| F-4 | grep漏れ | **成立可能** | ✓ 一覧チェックで防止可能 |
| F-5 | 非指定編集 | **成立可能** | ✓ ホワイトリストで防止可能 |
| F-6 | 禁止ファイル編集 | **成立可能** | ✓ git diff 確認で検出可能 |

---

## 反映: 対策の実装

本検証で成立した失敗原因（F-1 ～ F-6 すべて）に対して、以下の対策をタスク実施に反映：

### 対策 1: 行番号確認ワークフロー
```
前処理: 対象文書ごとに編集対象行を Read で確認
編集時: Edit 前に Read で行内容確認 → 一致確認 or 再検索 → Edit
後処理: 編集後に当該行を Read で確認 → 内容一致確認
```

### 対策 2: 参照実装の先読み
```
各 A区分 修正について編集前に:
1. Grep で対象実装ファイル特定
2. Read で実装内容確認（関数 signature / クラス定義）
3. 確認内容に基づき文書修正を決定
```

### 対策 3: 注記ブロック テンプレート化
```markdown
【冒頭包括注記】
> **【状態注記 2026-07-18】** 本書のうち [対象機能] は死滅コード監査により撤去済み（コミット [ハッシュ]）。本文書は設計記録として保存する。

【個別注記】
> **【撤去済み】** [シンボル] / [ファイル] は 2026-07-18 撤去（コミット [ハッシュ]）。
```

### 対策 4: 最終grep検証 シンボル一覧作成
```
編集完了後に下記シンボルについて grep 実施:
ParquetOHLCRepository, HtmlPresenter, generate_report, compare_run
OrderRow, cfmt（cfmtLocale 除外）, validateParams, Favorite
run_weekly_vol_band_cli, estimate_weekly_band, validate_strategy
gk_har_estimator, vol_band_parquet
```

### 対策 5: 編集対象ホワイトリスト確認
```
編集対象は以下 11 文書のみ:
1. .doc/MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md
2. .doc/backtest/BACKTEST_CLEAN_ARCH.md
3-11. [その他 9 文書]
```

### 対策 6: git diff 最終確認
```bash
git diff --name-only | grep -v "\.md$" && echo "ERROR!" || echo "✓ Only .md"
git diff --name-only | wc -l  # ≤ 11
```

---

## 残存リスク特定

本タスク実施中に対策できない残存リスク:

1. **参照実装の Read で、実装コメント等が古い可能性**: 実装コード本体で確認・コメントは参考程度

2. **複数ファイル同時編集による相互影響**: 各ファイルを順番に編集・確認・git diff で検証

3. **注記ブロックの正確性（コミットハッシュ・ファイルリスト）**: git log で確認・複数回チェック

4. **最終 grep で「類似シンボル」を見落とす可能性**: 正規表現を厳密にする（部分マッチ除外）

---

## 最終判定

✅ **本タスクの実施方針は合理的** — 以下で合格判定:

1. **Pre-mortem**: 6 つの失敗原因を能動的に推定 ✓
2. **証拠先行**: 各失敗原因について検証手段を判定前に提示 ✓
3. **成立判定**: 成立した原因 F-1 ～ F-6 すべて（対策実装で回避可能）✓
4. **反映**: 6 つの対策をタスク実施で適用・反映予定 ✓
5. **残存リスク**: 4 項目を明示・本タスク範囲外として記録 ✓

**結論**: 対策を実施することで、推定失敗原因はすべて回避可能。本タスク実施に進むことが可能。

