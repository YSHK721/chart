# 上流入力検証結果（upstream-input-validation）

## 上流入力の整理

| 種別 | 件数 | 内容 |
|---|---|---|
| 依頼者指示 | 1 | コミット 3 個計画・ファイルリスト・メッセージテンプレート・署名行フォーマット |
| 前段成果物 | 1 | develop = d1190dc2（ISSUE-511 段階 3 (a) マージ済み） |
| 他者レビュー指摘 | 0 | 該当なし |
| 既存合意の引き継ぎ | 0 | 該当なし |

**合計**: 2 件（上流入力あり）

## 前提抽出

### 依頼者指示
1. `materialize_m1_day(ticks, *, ref, price_basis)` 関数が `marketdata/tick_m1.py` に存在
2. `authoritative_day_m1(day, *, symbol, ref, data_dir)` の署名が `ref` パラメータを受ける
3. コミット 1 ファイル: `tick_m1.py` + `test_mt5_m1_append_api.py`
4. コミット 2 ファイル: `rebuild.py` + `test_mt5_rebuild.py` + `test_mt5_price_basis.py` + `test_mt5_rebuild_materialize.py`
5. コミット 3 ファイル: `ISSUE.md` のみ

### 前段成果物
1. develop の tip は d1190dc2（マージコミット）
2. 衝突なく新コミットをマージ可能

## 証拠先行検証

| # | 前提 | 実証手段 | 証拠 | 結果 |
|---|---|---|---|---|
| 1 | `materialize_m1_day` 関数存在 | `git diff HEAD -- marketdata/tick_m1.py` | `+def materialize_m1_day(ticks: pd.DataFrame, *, ref: str, price_basis: str) -> pd.DataFrame:` で +22 行新規関数確認 | ✅ 実証済み |
| 2 | `ref` パラメータ追加 | `git diff HEAD -- rebuild.py` | `def authoritative_day_m1(day: Any, *, symbol: str, ref: str, data_dir: Any)` で署名変更確認 | ✅ 実証済み |
| 3 | コミット 1 ファイル分離 | `git status --porcelain` + `git diff HEAD --stat` | tick_m1.py +22, test_mt5_m1_append_api.py +2 で分離確認 | ✅ 実証済み |
| 4 | コミット 2 ファイル分離 | `git diff HEAD -- [rebuild.py, test_*.py]` | rebuild.py ±22, test_mt5_rebuild.py ±4, test_mt5_price_basis.py ±2, test_mt5_rebuild_materialize.py +330 で分離確認 | ✅ 実証済み |
| 5 | develop tip 確認 | `git log --oneline develop -1` | d1190dc2 確認 | ✅ 実証済み |

## 判定結果

| 上流入力 | 前提 | 実証 | 判定 |
|---|---|---|---|
| 依頼者指示：コミット計画 | `materialize_m1_day` + `ref` 追加 + ファイル分離 | 全実証済み | **採用** |
| 前段成果物：develop = d1190dc2 | マージ可能性・衝突なし | develop tip 確認・マージ成功 | **採用** |

## 残存リスク特定

本タスク範囲外で後続作業に委ねるべき項目：
- なし

**完了判定**: ✅ 合格。全上流入力の前提が実証され、依頼指示と前段成果物の整合性が確認されました。
