# TDD 実行記録 — 再レビュー是正（NEW-A / NEW-B / NEW-C ・sim Phase 8）

対象ブランチ `feature/sim-phase8-tester-settings-ui`（起点 4fc3fb2）。

## §1 要件分析

| 要素 | 内容 |
|---|---|
| 機能 | (A) N-15 の UI 束縛を `none` へ訂正 (B) 発火条件の語彙一致の機械検査 (C) §18.5 の行順整理と R-10 追記 |
| 目的 | 正常な run への偽の断定の除去／片側改名の素通り遮断 |
| 入力 | `UnsupportedRule.ui` の宣言・front の `const TRIGGER_*`・fixture の `trigger` |
| 出力 | `activeUnsupported()` の該当集合（N-15 を含まない）／語彙一致の合否 |
| 制約 | `on_presence` を語彙から削らない／束縛キーは残す／既存の実行時 Fail-Stop は無改変／8000・8381 無停止 |
| 対象外 | N-15 の実行時判定の改変（`detect=None` の理由は不変）／規則 B（ISSUE-419）／UI 既定値（ISSUE-418） |

## §2 テストケース設計（新規 7 件）

| 群 | 種別 | 設計根拠 |
|---|---|---|
| E2E 1 件 | 異常系（偽陽性の否定）＋結合 | 実在範囲のカスタム期間で「N-15 が出ない」かつ「run は completed」＝偽陽性であったことの同時実証 |
| 束縛検定 2 件 | 仕様訂正の記録・不変条件 | N-15 が `none`／束縛キーは残す／`detect is None`（必要条件と十分条件の別を docstring に記録）・`on_presence` を語彙から削らない |
| 語彙検定 5 件 | 構造ガード＋自己検定 | front ⊆ 宣言／差分は `none` ちょうど 1 つ／fixture も同射程／抽出器の空振り検出／改名を別物として見る |

## §3 🔴 Red 結果

### NEW-A（真の Red）
- 実装事前状態の実証: `unsupported.py:344` が `mode=UI_TRIGGER_ON_PRESENCE`（Read で確認）。
- 初回実行: **`AssertionError: ['N-15'] / assert 'N-15' not in ['N-15']`**
  （新設 E2E `test_正しく指定したカスタム期間に偽の非対象告知を出さない`）。
  失敗は投入前の観測点で起きるため、Red 時点では run を 1 つも消費していない。
- 期待された理由か: **はい**。正常完走する指定に対して front が非対象を断定していた。

### NEW-B（穴の実測 → ガードの検出力で実証）
本件は「必ず通るテスト」を Red と称さない（AP.1 R-2 回避）。代わりに**欠陥の存在**を変異で先に実測した:
- サーバ側 `UI_TRIGGER_OFF_PROFILE` の値を改名 → **python 458 passed / npm 291 passed**（全ゲート素通り）。
- ガード新設後、同じ変異 → **4 failed / 1 passed**。front 側改名 → **2 failed / 3 passed**。復元 → 5 passed。

### Red 観測ゲート（4 軸）
| 軸 | 判定 |
|---|---|
| ① 過剰実装 | 非該当（NEW-A は Red 観測後に宣言を訂正） |
| ② 成功テスト先行 | 非該当（NEW-B は Red と称さず、欠陥を変異で実測した上でガードとして追加したと明示） |
| ③ 実装の事前残存 | 非該当（`on_presence` 宣言の存在を Read で確認したうえで訂正） |
| ④ assertion 弱体 | 非該当（NEW-A は E2E で run の completed も同時に主張。NEW-B は変異 2 種で検出力を実証） |

## §4 🟢 Green 結果

- NEW-A: `ui=UiTrigger(keys=("FromDate","ToDate"), mode=UI_TRIGGER_NONE)`。束縛キーは残す
  （畳んだ全一覧での所在を保つ）。`UI_TRIGGER_ON_PRESENCE` は語彙として残し、その理由を
  定数の docstring に記した。→ E2E **7 passed**・束縛検定 63 passed・`npm test` 291 passed。
- NEW-B: `sim_ui/tests/unit/test_settings_trigger_vocabulary.py`（5 件）。→ 5 passed。
- NEW-C: §18.5 を R-7 / R-8 / R-9 の順へ（内容無変更）＋ R-10 追記。
- 最小性: 実装の変更は宣言 1 行（`mode`）のみ。判定式・送出・実行経路・front の照合ロジックは無改変。

## §5 ♻️ Refactor 結果

構造改善なし（宣言 1 行の訂正とテスト追加のみ）。変異による検出力の実測は §3 に記載。
変異の復元は `cp`（git の破壊的コマンド不使用）。復元後 `git status` に実装差分なし。

## §6 完了判定

| 項目 | 判定 |
|---|---|
| テスト存在・実行可能 | ✔ E2E 7・語彙検定 5・束縛検定（engine 208 passed の内数）・web 291 |
| Red/Green の各出力 | ✔（NEW-B は Red ではなく変異実測であることを明示） |
| カバレッジ（偽陽性の否定・仕様訂正の記録・語彙一致・抽出器の自己検定） | ✔ |
| テスト名が機能・期待を記述 | ✔ |
| 回帰 | ✔ 既存テストの緩和 0。N-15 参照箇所は仕様訂正として更新し、その別（必要条件／十分条件）を docstring に記録 |
| 横断アンチパターン | 非該当（skip / xfail / カバレッジ偽装なし） |

## 違反リスト
**空集合**。

## 残存
- N-15 は実行時に発火しても**投入前には予告できない**（`detect=None` の帰結）。理由は
  `failure_reason` と告知の全一覧に残るため沈黙ではない。
- E2E `custom_range` の日付はデータセットの実在範囲に依存（既存の窓検定と同じ前提）。
- ブラウザ目視は依頼者のスタック再起動後。ISSUE-418 / ISSUE-419 は未着手（承認事項）。
