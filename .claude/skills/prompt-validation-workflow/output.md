# 自己レビュー出力（prompt-validation-workflow）— profit_rmm_macd 因果窓化 TDD

## Pre-mortem 分析（最も可能性の高い失敗原因）

本成果物（因果ローリング窓化 + EMA 非汚染）が本番で失敗するとしたら、最も可能性の高い死因：

1. **F1: EMA の repaint 残存** — 因果 span で level_count は repaint しないが、EMA を「全長 NaN 除外せず」または「start 固定せず」に実行すると、過去バーの EMA が末尾追加で変化し repaint が再発する。
2. **F2: 既存テストの退化（false green）** — 因果デフォルト（W=120）で短い fixture（n=20<W）が all-NaN になり、`assert_allclose(equal_nan=True)` が NaN==NaN で trivially pass。discriminating 性を失った合格を「合格」と誤認する。
3. **F3: 全 NaN 境界での汚染再発** — n<window で level_count 全 NaN のとき `np.argmax(isfinite)` が 0 を返し、start=0 で全長 EMA を実行 → NaN 汚染が全出力に伝播。
4. **F4: window=None の数学的非同一性** — 全期間版が従来挙動とずれ、回帰を起こす。

## 証拠先行検証

| 死因 | 実証手段 | 出力 | 判定 |
|---|---|---|---|
| F1 | 合成 n=500・W=120 で 4 cut 点(300/350/400/450)×全 bar の `\|full.hist[bar]-short.hist[bar]\|` の最大値 | **0.0**（全バー・全 cut で完全不変） | 棄却（repaint 残存せず）。start で EMA 再シードするため過去 EMA は未来非依存 |
| F1' | 実データ jp225 #300（履歴 400→1379） | hist/macd/signal 全て bit 一致（invariant=True） | 棄却 |
| F2 | n=20 fixture を既定 W=120 で実行 | level_count/macd 全 NaN を確認 → 該当 5 テストに `window=None` ＋ `assert np.all(np.isfinite(...))` を追加し退化を構造的に排除 | 成立 → 修正反映済 |
| F3 | `np.argmax(np.isfinite(全NaN))` ＝ 0 を確認 → core で `if not finite_mask.any(): start=n`（EMA 非実行）を明示分岐 → n=50<W=120 テストで全チェーン all-NaN 検証 | テスト `test_short_series_below_window_yields_all_nan_chain` pass | 成立 → 修正反映済 |
| F4 | window=None で fast/slow が全長 `exponential_ma_on_buffer` と bit 一致、macd==slow-fast 一致、level_count が sister(window=None) と bit 一致 | 全て True | 棄却 |

## 検証と反映

- **F1 / F1' / F4**: 実証により棄却（成果物は正しい）。証拠は repaint 最大差 0.0、実データ #300 不変、window=None 全長 EMA 一致。
- **F2**: 成立。退化 false-green を 5 テスト（parity×2 / macd 符号 / histogram 係数 / EMA 連鎖）で `window=None` 明示 ＋ `isfinite` ガード追加により修正反映済み。修正後 42 passed。
- **F3**: 成立。全 NaN 境界の汚染を `start=n` 明示分岐で遮断、専用テストで固定済み。

## 残存リスク

- **R1**: 既存 `test_rmmmacd.py::test_builds_three_columns_matching_core` は build==core の**配線**テストとして残るが、既定 W=120・n=20 では両辺 all-NaN で値の discriminating 性は無い（配線の同一性のみ担保）。因果値の discriminating は `test_causal_window.py`（core レベル）でカバー済み。配線テストの値検証強化は後続フェーズに委ねる（本タスク範囲外・スコープ追加回避）。
- **R2**: rolling_span / oscillator_span / _series_* の sister との重複は「verbatim 複製」設計（in-package 自己完結）の意図的選択。共通化は cross-package 依存を生むため本タスクでは不実施（設計判断・後続の合意要）。
- **R3**: jp225 の volume=1000.0 定数補完は MFI 計算へ定数寄与する。仕様妥当性（実 volume 不在の影響）は本タスク範囲外。
- **R4**: コミット未実施（依頼指定）。後続フェーズで実施。
