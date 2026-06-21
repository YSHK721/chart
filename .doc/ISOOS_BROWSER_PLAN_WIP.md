# ブラウザ・シミュレーション + IS/OOS 機能 — 工程計画（暫定 / WIP）

> ⚠️ 本書は**一時的な作業記録（WIP）**。合意済みの確定仕様ではない。確定後は
> `.doc/backtest/` の formal 仕様（`BACKTEST_DESIGN.md` 等）へ昇格・移設する。
> 記録日: 2026-06-20。

## 0. 目的・背景

既存の純 Python バックテストエンジン（`simulator/`・クリーンアーキ・MT5 bit-exact 突合済）に対し、
次の 2 機能を追加したい:

- **(1) チャート上の売買ポイント可視化** — シミュレーション結果の売買ポイントを視覚的に確認。
- **(2) IS/OOS 機能** — 単純分割 + 最適化 + ウォークフォワードの 3 段階すべてを範囲とする。

実行形態は**「B. サーバ実行 + ブラウザ UI」**を採用（A. Pyodide/WASM 完全クライアントは将来段階移行候補）。

## 1. 実行形態の決定 — B（サーバ + ブラウザ UI）

既存のインジケーター UI（`indigators/indicator_ui/api/framework/server.py`）が **stdlib のみ**
（`http.server`/`json`/`urllib`/`pathlib`・**新規依存禁止**）の薄殻 HTTP サーバ + `web/` の
ES Modules（バンドル不要）+ lightweight-charts で既に成立している。**この既存パターンに統一**し、
技術スタックを増やさない。

| 項目 | 統一方針（依存追加なし） |
|---|---|
| サーバ | `http.server` 薄殻を踏襲。`POST /simulate` を追加し `simulator.main.run_backtest` に委譲 |
| フロント | 既存 `web/` の ES Modules + lightweight-charts に config フォーム・結果パネルを追加 |
| データ入力 | 既存 `/candles` の datasetRef ホワイトリスト方式を流用（`marketdata/` から選択） |
| 結果描画 | `stats.json` 相当を JSON 応答 → チャート上にトレード/損益を重畳 |

HTTP 化の起点は Composition Root の `run_backtest(**meta)`（`simulator/main/__init__.py`）。

## 2. アクターマップ（変更理由の軸で分離）

| アクター | 担当 | 本件での扱い |
|---|---|---|
| **A. エンジン保守者** | 約定突合・MT5 bit-exact・tick model | **無改変・再利用**（`run_backtest` を部品化） |
| **B. 結果閲覧者/分析者** | Presenter・チャート・UI | **(1) がここ** + IS/OOS 比較表示 |
| **C. データ供給** | marketdata / repository | 再利用 + 期間分割の範囲指定 |
| **D. 検証方法論**（新規） | IS/OOS 分割・WF 窓設計（anchored/rolling・窓幅・ステップ） | **(2) で新設 UC** |
| **E. 最適化**（新規） | パラメータ探索アルゴリズム + 目的関数 | **(2) で新設 UC + Port** |

→ (1) = B、(2) = D + E。3 者は独立軸。混ぜずにモジュール分離する。

## 3. レイヤリング（すべて committed エンジンの「上」に載る）

```
[新] usecase/walk_forward.py   ← D: 窓ごとに IS最適化→凍結→OOS評価 を反復・統合
[新] usecase/optimize.py       ← E: 探索空間×目的関数で IS を探索・best params 返却
        ├─ ParameterSearchPort  (grid / random / … を差替可能)
        └─ ObjectivePort        (PF / NetProfit / Sharpe … 指標抽出)
[既] usecase/run_backtest.py   ← A: 1run プリミティブ（無改変・N回呼ばれるだけ）
[新] presenter/is_oos.*         ← B: 窓別 IS vs OOS・劣化率・OOS連結エクイティ
[新] presenter/trade_markers.*  ← B: (1) 売買マーカー JSON（result.trades を変換）
[既] http.server 薄殻 + web/    ← B/C: /simulate・/walkforward・/candles を追加（依存ゼロ）
```

委譲構造により、D の窓設計変更も E の探索法変更もエンジンに波及せず、**MT5 突合資産を保護**
（「破壊的変更禁止」に整合）。

## 4. 設計上の留意点

- **同期/非同期の緊張**: 単一 run・単純 IS/OOS は同期応答で可。最適化/ウォークフォワードは
  `run_backtest` を「パラメータ数 × 窓数」回呼ぶため重く、**進捗付き非同期ジョブ**
  （`POST /walkforward` → job_id → ポーリング）が現実的。N 回実行の計算コスト最小化
  （探索空間上限・キャッシュ・並列）を仕様へ明記する。
- **(1) の実現性**: `result.trades`（`TradeRecord`: side/entry_time/exit_time/entry_price/
  exit_price/exit_reason/volume）に描画要素が揃う。エンジン無改変で Presenter 拡張のみ。

## 5. 工程シーケンス（フェーズ）

- **Phase 0 — MT5 IS/OOS bit-exact 突合基盤** … ✅ **完了（2026-06-20）**
  - `simulator/tests/confirmation/2026-04_stop-probe_oos/`
  - OOS（forward 04.15-）: 2438/2438・net -4020・balance 5980 ✅（`reconcile.py`）
  - IS（in-sample 04.01-14）: 5224/5224・net +11370・balance 21370 ✅（`reconcile_is.py`）
- **Phase 1 — IS/OOS オーケストレーション実装（アクター D/E）**
  - `usecase/optimize.py` + `ParameterSearchPort`/`ObjectivePort`
  - `usecase/walk_forward.py`（split → optimize → validate の反復統合）
  - `presenter/is_oos.*`（窓別 IS vs OOS・劣化率）
- **Phase 2 — ブラウザ UI + 可視化**
  - 既存 stdlib サーバへ `POST /simulate`・`POST /walkforward`（非同期 + ポーリング）追加
  - `web/` に config フォーム・結果パネル・IS/OOS 比較ビュー
  - (1) 売買マーカー描画（`presenter/trade_markers.*` → lightweight-charts marker）

## 6. 申し送り

- `build_interactor` に trading_end 相当が無く、IS の終端はバー truncation（`bars_m1_is.csv`）で代替。
  IS/OOS 自動化では「期間スライス」を UC 側で明示的に扱う必要がある。
- 仕様の確定は Phase 単位で `spec-items-clarifier`（9 項目）→ formal 化を想定。
