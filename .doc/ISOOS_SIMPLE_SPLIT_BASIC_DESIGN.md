# IS/OOS 単純分割（Simple Split）基本設計書

## 1. 文書情報

- 作成日：2026-06-20
- バージョン：v0.2.0
- 作成者：system-basic-design エージェント
- 承認者：（未承認・レビュー待ち）
- 変更履歴：
  - v0.1.0 (2026-06-20) 初版。IS/OOS サブフェーズ1「単純分割」の基本設計。
  - v0.2.0 (2026-06-20) spec-reviewer 指摘（B-1/B-2/C-1/C-2/H-1/H-2/H-3/M-1/M-2/M-3/L-1/L-2）を決定論的に解消。呼出経路を `controller._interactor.execute(request)` に一意化（B-1）、IS truncation を option b（`request.bars` 差し替え・registry 非再構築）へ確定（B-2/H-3）、劣化指標を ratio+delta 両格納に統一（C-2）、NFR バイト同一の保証境界を限定明記（C-1）、`is_trading_start` を必須フィールド化（H-1）、出力先検証関数とデータ非波及の計測可能手段を追加（H-2）、空区間判定式・OOS 実証行参照・出力整形方針・tempfile 規約・時刻正規化 TBD を確定（M-1/M-2/M-3/L-1/L-2）。旧 TBD-1/TBD-2/TBD-4 を解消済みへ更新。

---

## 2. プロジェクト概要

### 2.1 システム概要

- **位置付け**：既存の committed バックテストエンジン（`simulator/`・クリーンアーキテクチャ・MT5 bit-exact 突合済）の上に載るオーケストレーション層。単一の価格データセット・分割境界（split 日時）・固定戦略パラメータを入力に、エンジンを **IS 区間 `[start, split)` と OOS 区間 `[split, end)` で別々に実行**し、両区間の成績（`BacktestStats`）を並列レポート＋劣化指標として出力する。
- **解決する業務課題**：戦略パラメータが「学習区間（IS）でのみ機能し、未知区間（OOS）で劣化する」過剰最適化（カーブフィッティング）の有無を、同一パラメータの IS/OOS 並列評価で定量把握する。本サブフェーズは**最適化を行わない**（同一パラメータを両区間で評価する最小段）。
- **3 サブフェーズ中の位置**：IS/OOS 機能は「①単純分割 → ②最適化 → ③ウォークフォワード」の 3 段で構成され、本書は①のみを対象とする（`.doc/ISOOS_BROWSER_PLAN_WIP.md` の Phase 1 の最小単位）。②③は後段で本設計を土台に拡張する。

### 2.2 開発目的・背景

- **背景**：Phase 0（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §5）で、MT5 が 2026-04 を IS(04.01-04.14)/forward(04.15-) に分割した区間について、`simulator/tests/confirmation/2026-04_stop-probe_oos/` の `reconcile.py`（OOS）・`reconcile_is.py`（IS）が実機 bit-exact（OOS 2438/2438・net -4020・balance 5980／IS 5224/5224・net +11370・balance 21370）を達成済み（同ファイルの docstring・実装で実証）。本サブフェーズはこの「IS/OOS 分割を手書きの使い捨てスクリプト 2 本で達成した先例」を、再利用可能な単一オーケストレーション UC へ一般化する。
- **達成したい目標**：committed エンジンを無改変のまま、1 回の呼び出しで IS/OOS 双方の `BacktestStats` と劣化指標を返す UC を新規追加する。本 UC は後段の `usecase/optimize.py`・`usecase/walk_forward.py`（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §3）が「split → run」プリミティブとして再利用する土台となる。

### 2.3 適用範囲・制約条件

#### 機能要件サマリー（要件 ID 一覧）

| 要件 ID | 概要 |
|---|---|
| FR-01 | 分割境界（split 日時）を入力として受け取る |
| FR-02 | IS 区間 `[start, split)` でエンジンを実行し `BacktestStats` を得る |
| FR-03 | OOS 区間 `[split, end)` でエンジンを実行し `BacktestStats` を得る |
| FR-04 | IS／OOS 双方の成績を並列レポートとして出力する |
| FR-05 | 劣化指標（OOS vs IS の比 / 差分 Δ）を算出して出力する |
| FR-06 | 最適化を行わない（同一の固定パラメータを両区間に適用する） |

#### 非機能要件サマリー（数値目標を含む）

| 区分 | 数値目標 |
|---|---|
| 性能（NFR-P1） | エンジン実行は IS/OOS の 2 回（並列分割により総バー数は単一フル run と同程度。劣化指標算出は `BacktestStats` 上の O(1) 算術のみ） |
| 決定論性（NFR-D1） | 同一入力（データ・split・params・config）に対し IS/OOS の `BacktestStats` がバイト同一で再現される（エンジンの決定論性 PROCESS §7 を継承）。**バイト同一の保証境界（C-1）**：結合テストで実証されるのは `pending_lifecycle`（StopEntryProbe・every-tick 経路。`run_backtest.py` L157-160 で `pending_lifecycle=True` 時に `_execute_every_tick` へ分岐し L488 `enumerate(bars)` を走る経路）**のみ**。bar-mode 経路（L203 `enumerate(bars)`・`pending_lifecycle` 非設定）は先例 reconcile 突合の対象外のため、別途**単体テストで境界整合（IS truncation 後の `request.bars` 差し替えが位置インデックス整合を保つこと）を担保**する。両経路とも `enumerate(bars)` の位置インデックスを strategy が `.iloc[bar_index]` で参照する点は共通（NFR-D1 の境界整合の根拠）。 |
| 既存データ非波及（NFR-S1） | `marketdata/`・`simulator/tests/fixtures/`・`simulator/tests/confirmation/`・既存生成物への書き込み 0 件。**検証は計測可能手段に落とす（H-2）**：(1) 出力先検証関数（§6.5）が上記プレフィクスへの書込みを拒否すること、(2) 結合テストで「実行前後に既存データディレクトリ配下ファイルの mtime が不変」を assert すること、の 2 点で実証する。 |
| committed 無改変（NFR-S2） | `simulator/domain`・`simulator/usecase`（既存ファイル）・既存 `simulator/adapter`・`simulator/main` の差分 0 行。`controller._interactor`（private 属性）の**読み取り利用のみで改変なし**であり C2「committed 無改変」と矛盾しない（§6.1 保証境界・B-1）。 |

#### 制約条件（技術 / 運用 / プロジェクト規約）

| 区分 | 制約 |
|---|---|
| 絶対制約 C1（運用） | 既存データの改変・波及を禁止する。`marketdata/`・`simulator/tests/fixtures/`・`simulator/tests/confirmation/`・既存生成物は読み取り専用。出力は新規パスのみ。 |
| 絶対制約 C2（技術・アーキテクチャ） | committed エンジン無改変。`simulator/domain`・`simulator/usecase`（既存ファイル）・既存 `simulator/adapter`・`simulator/main` を編集しない。本機能は committed の公開 IF（`run_backtest` / `build_interactor` / `RunBacktestRequest`）の上に載るオーケストレーションとして**新規ファイル**で実現する。新 Port/UC は `ports.py` 等を編集せず新規ファイルへ置く。 |
| 絶対制約 C3（技術） | 技術スタック追加禁止（純 Python＋既存依存のみ）。 |
| プロジェクト規約 C4 | `.claude/CLAUDE.md`：指示範囲外の変更・破壊的変更を禁止する。 |

#### 設計上の課題と技術的リスク

| ID | 課題／リスク | 備考 |
|---|---|---|
| 課題-1 | IS 区間の**終端**（`split` 以降を取引させない）を committed IF でどう表現するか。`build_interactor` には取引開始境界 `trading_start` はあるが**取引終端 `trading_end` が存在しない**（`simulator/main/__init__.py` の `build_interactor` シグネチャ L256-285 で実証。終端引数なし）。先例 `reconcile_is.py` は IS 用に終端済み別 CSV（`bars_m1_is.csv`）を用意して回避した（同ファイル docstring L9-12 で実証）。 |
| リスク-1 | warmup 区間の扱い。IS/OOS とも指標 seed 収束のため `trading_start` 以前のバーが必要（先例は両者とも 03-23 起点の同一 warmup を含む CSV を使用。`reconcile.py` L35・`reconcile_is.py` L35 で実証）。OOS 実行では IS 取引区間が warmup として機能するため、split 前バーを入力に含める必要がある。**区間定義（H-1）**：データ先頭〜`is_trading_start`＝IS warmup、`is_trading_start`〜`split`＝IS 取引区間、`split`〜end＝OOS 取引区間、データ先頭〜`split`＝OOS warmup。 |
| リスク-2 | 時刻型整合。`bar.time` と `trading_start` は比較可能な型（`numpy.datetime64` / epoch int）を要する（`run_backtest.py` L82-84 の docstring で実証）。先例は `pd.Timestamp(...)` を `trading_start` に渡している（`reconcile.py` L125）。スライス境界 split の型を入力データの `bar.time` 型と整合させる必要がある。 |

---

## 3. システムアーキテクチャ

> 出力元：S-1 採用パターン選定 ／ S-2 アーキテクチャ設計

### 3.1 全体構成図

```
┌─────────────────────────────────────────────────────────────┐
│ [新規] 実行入口層（tools）  simulator/tools/run_is_oos_cli.py │
│   - 価格 CSV を読み取り専用ロード（列ブリッジは tmp/新規へ）   │
│   - split / params / config を引数で受領                      │
│   - 新規 UC を呼び、結果を新規出力先のみへ書く                │
└───────────────────────────┬─────────────────────────────────┘
                            │ 呼び出し（依存方向：tools → usecase）
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ [新規] オーケストレーション UC                                │
│   simulator/usecase/run_is_oos.py                             │
│   - RunIsOosRequest（data + split + is_trading_start + ...）  │
│   - run_segment コールバックを IS/OOS の 2 回呼ぶ（B-1/H-3）  │
│   - DegradationReport（ratio+delta 両格納・C-2）を算出        │
│   - slice_is_bars(bars, split): IS head 切り純関数（B-2）     │
└───────────────────────────┬─────────────────────────────────┘
                            │ 部品として再利用（committed 公開 IF）
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ [既存・無改変] committed エンジン                             │
│   simulator/main.build_interactor(...) -> (controller, req)  │
│   controller._interactor.execute(request) -> BacktestResult  │
│     ↑ 直接呼ぶ（controller.run() は使わない＝B-1）。          │
│       controller.run は data_path から bars を再ロードし      │
│       request.bars を使わないため truncation が無効化される   │
│       （adapter/controller.py:50 が根拠）。                   │
│   RunBacktestRequest（trading_start で取引開始境界を制御。    │
│       bars を UC が差し替え可＝IS truncation 注入点）         │
│   BacktestResult.stats : BacktestStats                       │
└─────────────────────────────────────────────────────────────┘
```

- **データフロー**：価格データセット（読み取り専用）→ tools が DataFrame/Bar 化に必要な前処理 →
  tools が `build_interactor(...)` で IS/OOS それぞれ `(controller, request)` を取得 →
  IS は `request.bars` を `slice_is_bars(bars, split)` で head 切り（split 以降を除去）して差し替え／OOS は `request.bars` 無改変で `trading_start=split` を設定 →
  **`controller._interactor.execute(request)` を直接呼ぶ**（B-1）→ 2 つの `BacktestStats` →
  劣化指標算出 → 新規出力先へレポート。

### 3.2 アーキテクチャパターン選択理由

- **採用パターン**：クリーンアーキテクチャ準拠の「オーケストレーション UC を committed エンジンの上に重ねる（委譲・合成）」パターン。新規 UC は committed の公開 IF を**部品として呼ぶだけ**で、エンジン内部に手を入れない。

| 評価軸 | 案A：新規オーケストレーション UC（採用） | 案B：committed `execute` に区間制御を追加改修 | 案C：tools スクリプトに全ロジックを直書き（UC なし） |
|---|---|---|---|
| 要件適合（IS/OOS 2 回 run + 劣化） | ○ UC が 2 run と劣化算出を統括 | ○ | △ 再利用不能・後段拡張不可 |
| C2 committed 無改変 | ○ 既存ファイル 0 行差分 | ✕ `run_backtest.py`/`models.py` を改修＝C2 違反 | ○ |
| 後段（最適化/WF）の土台化 | ○ `optimize`/`walk_forward` が UC を再利用 | △ | ✕ tools にロジックが閉じ再利用不能 |
| クリーンアーキ依存方向（usecase→domain のみ） | ○ 新規 UC は domain のみ依存 | ○（既存層内） | △ tools は main 依存可だが責務肥大 |
| MT5 突合資産の保護 | ○ エンジン無波及 | ✕ bit-exact 経路を改変するリスク | ○ |

- **採用根拠**：案 A は C2（committed 無改変）と「後段サブフェーズの土台化」（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §3 のレイヤリング図に整合）を同時に満たす唯一案。
- **棄却理由**：案 B は `run_backtest.py`/`models.py` の改修が必須となり C2 違反かつ MT5 bit-exact 経路を改変するリスク（プロジェクト規約「破壊的変更＝共有リソースの破壊的変更」に抵触）。案 C は再利用不能で後段 `optimize`/`walk_forward` が本ロジックを呼べず土台化要件を満たさない。
- 出典：（実務的推奨／仮説）＋ プロジェクト規約（C2・`.doc/ISOOS_BROWSER_PLAN_WIP.md` §3）。

### 3.3 技術スタック詳細

| 層 | 採用技術 | バージョン | 代替候補 | 採用根拠 |
|---|---|---|---|---|
| オーケストレーション UC | 純 Python（`@dataclass`・標準ライブラリ） | 既存リポジトリと同一（追加なし） | pydantic 等の検証層 | C3 技術スタック追加禁止。usecase 層は pydantic 非依存（`models.py` L1-9 docstring で実証）。 |
| 部品（エンジン） | committed `simulator.main` / `simulator.usecase` | 既存 | — | C2 により無改変で再利用 |
| 入力前処理（tools） | `pandas`（main 層内に閉じる既存利用） | 既存 | — | 先例 `export_trade_markers.py` が tools での pandas 利用を確立済（L27 で実証）。pandas は composition root=main/tools 内に閉じ usecase へ漏らさない（`main/__init__.py` L106-112 の方針を継承）。 |

- 技術スタックの追加・バージョン変更は 0 件（C3 遵守）。

### 3.4 レイヤー構成・責務分担

| レイヤー | 責務 | 依存先 | 出典／根拠 |
|---|---|---|---|
| tools（実行入口・新規） | CLI 引数解釈・読み取り専用ロード・新規出力先への書き込み・新規 UC 呼び出し | usecase（新規 UC）・main（`build_interactor`） | プロジェクト規約（`export_trade_markers.py` の C3「main 無改変＝Composition Root 利用側」パターン L6 で実証） |
| usecase（オーケストレーション・新規 `run_is_oos.py`） | IS/OOS 区間スライス・2 run 統括・劣化指標算出。domain のみ依存（adapter/framework/main を import しない） | domain | 公式設計原則（クリーンアーキ：usecase は domain のみ依存。`run_backtest.py` L7-8 docstring で既存規約として実証） |
| usecase（既存・無改変） | 1 run プリミティブ（`RunBacktestInteractor.execute`） | domain | C2（committed 無改変） |
| domain（無改変） | Bar / TradeRecord / BacktestStats 等の VO | （なし） | C2 |

- **依存方向**：tools → usecase（新規）→ domain。新規 UC は committed `build_interactor`（main 層）を直接呼ばず、**tools 層が `build_interactor` で `(controller, request)` を構築し、UC へは「（full or truncated）bars を受けて 1 区間を `controller._interactor.execute(request)` で実行する」コールバック（run_segment・H-3 契約）を委譲で渡す**（§6.1 で確定）。これにより「usecase→main」の逆依存（クリーンアーキ違反）を避ける。
  - 出典：公式設計原則（依存方向の内向き規律）。

---

## 4. 機能設計

> 出力元：S-3 機能設計

### 4.1 機能一覧・優先度

| 機能 ID | 機能名 | 概要 | 優先度 | 対応要件 ID |
|---|---|---|---|---|
| F-01 | 分割境界受領 | split 日時を入力として受け取り、データ `bar.time` 型と整合させる | 高 | FR-01 |
| F-02 | IS 区間実行 | `[start, split)` でエンジンを 1 回実行し `BacktestStats` を取得 | 高 | FR-02, FR-06 |
| F-03 | OOS 区間実行 | `[split, end)` でエンジンを 1 回実行し `BacktestStats` を取得 | 高 | FR-03, FR-06 |
| F-04 | 並列レポート出力 | IS/OOS の `BacktestStats` を併記形式で出力 | 高 | FR-04 |
| F-05 | 劣化指標算出 | OOS vs IS の比・差分 Δ を主要指標について算出 | 高 | FR-05 |

### 4.2 機能詳細仕様（主要機能のみ）

#### F-02 / F-03 区間実行（IS / OOS 共通）

- **入力**：価格データ参照（全期間バー列・warmup 含む）、split 日時、`is_trading_start`（IS 取引開始境界・必須・H-1）、固定戦略パラメータ群（`build_interactor` の引数：`lot_size`/`stop_loss_points`/`take_profit_points`/`entry_offset_points` 等）、決定論 config（`config_overrides`）、`initial_deposit`・`symbol_spec` 相当値。
- **前提条件**：
  - 全期間バー列が `start <= is_trading_start <= split <= end` を満たす（split・is_trading_start がデータ範囲内）。
  - split・is_trading_start の時刻型が `bar.time` と比較可能（`numpy.datetime64` / epoch int。`run_backtest.py` L82-84 で実証された制約）。
- **後条件**：IS／OOS それぞれについて `BacktestResult`（うち `stats: BacktestStats`）を得る。
- **呼出経路（B-1・確定）**：IS/OOS とも tools 層が `build_interactor(...)` で `(controller, request)` を取得し、**`controller._interactor.execute(request)` を直接呼ぶ**。`controller.run()` は使わない。根拠：`controller.run()` は `self._market_data.load(source_ref, ...)`（`adapter/controller.py:50`）で `data_path` から bars を再ロードし、UC が差し替えた `request.bars`（truncation 後）を使わないため、IS truncation が無効化される。`_interactor` は private 属性だが**読み取り利用のみ・改変なし**で NFR-S2/C2 と矛盾しない。先例 `reconcile_is.py:127`・`reconcile.py:127`・`export_trade_markers.py:10` が同経路を実証。
- **区間スライス方式（実装可能水準で確定）**：
  - **OOS 区間**：committed の `trading_start` を `split` に設定する（`build_interactor(... trading_start=split)`）。エンジンは `bar.time < trading_start` のバーを「指標 seed 収束のみ・トレード/equity/stats から除外」する warmup として扱う（`run_backtest.py` L79-84・L208-209 の `if trading_start is not None and bar.time < trading_start: continue` で実証）。split 以前（IS 取引区間＋IS warmup）が OOS の warmup となる。**全期間バー列（`request.bars` 無改変）をそのまま入力**する。
  - **IS 区間（option b・確定・B-2/H-3）**：エンジンに取引終端引数（`trading_end`）が存在しない（課題-1）ため、**全期間バー列を `bar.time < split` で head 切り（末尾切り＝head 保持）した IS 用バー列で `request.bars` を差し替える**。`trading_start=is_trading_start` を設定する。registry は**再構築しない**。
    - **option b を採用する理由（option a＝tmp 新規 CSV 生成を棄却する理由）**：IS truncation は「`bar.time < split` の末尾切り（head 保持）」であり、保持されるバーの**位置インデックス 0..k-1 が full df 由来の指標 registry と整合する**。エンジンは `enumerate(bars)`（`run_backtest.py:203`・every-tick は L488）の位置インデックス `bar_index` を strategy へ渡し、strategy は `indicators.get(name).iloc[bar_index]`（`tc24051901.py:42`・`pro_fit_band.py:69` 等）で指標を引く。registry は full df から事前計算した `pandas.Series` を位置インデックスで保持し `update()` は no-op（`registry.py:53`・`get()` は L28-35）。head 切りは位置 0..k-1 を不変に保つため、**full df 由来の registry を再構築せずそのまま使える**。これに対し option a（IS 用 tmp CSV を `build_interactor(data_path=...)` で渡す）は registry も IS 長で再計算され、committed build ロジックの再実行コストと先例（`bars_m1_is.csv` 手作業分割）への退行を招くため棄却する。
    - **byte-exact 整合の決め手（causal 指標）**：登録指標は前方再帰の causal 計算であり、`ema[i]` は `price[0..i]` のみに依存する（`main/__init__.py` L211-225 `_ema_series`＝seed=price[0]・α=2/(period+1) の前方 EMA）。`open`/`spread` は当該バー値（位置一致）。head 切りは位置 0..k-1 の `price` を不変に保つため、full df から計算した `ema[0..k-1]` と IS 長 df から計算した `ema[0..k-1]` は**bit-identical** になる。よって registry を再構築しても再構築しなくても位置 0..k-1 の値は同一であり、再構築しない選択（option b）はコストを増やさず byte-exact を保つ（先例 `bars_m1_is.csv` の別 registry とも値一致）。
    - truncation は **UC 層の純関数 `slice_is_bars(bars, split)`**（入力 bars 列・split を受け、`bar.time < split` を満たす先頭連続区間を返す）として定義する（H-3）。副作用なし・domain のみ依存。
    - 先例 `reconcile_is.py` は IS 専用の終端済み CSV（`bars_m1_is.csv`・docstring L9-12）でこれを実現したが、本設計は `slice_is_bars` による in-memory head 切りで IS 専用 CSV の事前準備を不要化し、単一データセットから IS/OOS 双方を導出する（先例の手作業 CSV 分割を一般化）。
- **例外条件（M-1・確定）**：空区間拒否条件を「**IS バー数（`slice_is_bars` の結果長）≥ 1 かつ、OOS で `bar.time >= split` のバー数 ≥ 1**」を満たさない場合に UC が事前検証で拒否（エンジン呼び出し前に中断）する。

#### F-05 劣化指標算出

- **入力**：IS の `BacktestStats`、OOS の `BacktestStats`。
- **処理（C-2・確定）**：**全主要指標について `ratio` と `delta` の両方を格納する**（指標の尺度別に「比 or 差分」を使い分けず、両方を提示し解釈は利用側に委ねる）。対象指標は `BacktestStats`（`models.py` L91-142）の実在フィールドに限定する：
  - `profit`（純損益）、`profit_factor`、`recovery_factor`、`expected_payoff`、`sharpe_ratio`、`trades`（いずれも `models.py` L98-105 で実在を確認）。
  - 各指標について `ratio = OOS_x / IS_x`（IS_x ≠ 0）、`delta = OOS_x − IS_x` を両方算出して格納する。
  - ゼロ除算（IS_x = 0）は `ratio` を未定義（None / 明示マーカー）として格納し、`delta` は常に格納する（ratio が未定義でも delta は提示可能）。
- **後条件**：`DegradationReport`（指標ごとに `{is_value, oos_value, ratio, delta}` を保持する集合）を得る。
- **判断（実務的推奨／仮説）**：profit_factor・expected_payoff・sharpe_ratio は OOS で IS より低下するほど過剰最適化の疑い。閾値判定（合否）は本サブフェーズでは行わず**指標の提示のみ**（ratio/delta の解釈と合否ロジックは後段②最適化で目的関数として導入）。

### 4.3 処理フロー図

```
[start] CLI/呼び出し
  │ 入力: data参照, split, is_trading_start, params, config, initial_deposit, symbol_spec
  ▼
(1) 入力検証: start <= is_trading_start <= split <= end / 時刻型整合
  │           空区間判定（M-1）: IS バー数 >= 1 かつ OOS(bar.time>=split) 数 >= 1
  │  NG → 中断（区間空 / 型不整合）
  ▼
(2) tools が build_interactor(...) で IS/OOS の (controller, request) を取得（全期間バー列を含む）
  ▼
(3) slice_is_bars(bars, split): bar.time < split の head 区間を返す（UC 層純関数・H-3）
  │
  ├─(4a) IS run: request.bars を IS用バーへ差し替え + trading_start=is_trading_start
  │        → controller._interactor.execute(request) → IS_stats（B-1: controller.run 不使用）
  │
  └─(4b) OOS run: request.bars 無改変 + trading_start=split
           → controller._interactor.execute(request) → OOS_stats（B-1）
  ▼
(5) 劣化指標算出: 全主要指標について ratio(OOS/IS) と delta(OOS−IS) を両格納（C-2）
  ▼
(6) 並列レポート構築（IS_stats | OOS_stats | 劣化）→ 出力先検証（§6.5・H-2）→ 新規出力先へ書き込み
  ▼
[end]
```

### 4.4 業務フロー・ユースケース

- **主アクター**：D. 検証方法論（IS/OOS 分割の担当・`.doc/ISOOS_BROWSER_PLAN_WIP.md` §2）。
- **ユースケース**：「分析者が、ある固定パラメータ集合の堅牢性を、データを学習区間と検証区間に分けて 1 回の操作で評価する」。最適化（E）は本 UC を呼ぶ将来の上位ユースケースであり、本サブフェーズの対象外。

---

## 5. データ設計

> 出力元：S-3 データ設計

### 5.1 データモデル概要（概念・実装非依存）

- **入力**：`{ 価格データ参照, split, 固定戦略パラメータ, 決定論 config, 口座初期値, シンボル仕様 }`。
- **中間**：`{ IS 用バー列, OOS 用全期間バー列 }`、`{ IS_BacktestResult, OOS_BacktestResult }`。
- **出力**：`{ IS_BacktestStats, OOS_BacktestStats, DegradationReport }`。

### 5.2 主要エンティティ定義

| エンティティ | 概要 | 主要属性 | 関連エンティティ |
|---|---|---|---|
| IsOosInput（新規・概念） | 単純分割の入力一式 | 価格データ参照、split（時刻）、is_trading_start（必須・H-1）、戦略パラメータ群、config、initial_deposit、symbol 仕様 | SplitBoundary |
| SplitBoundary（新規・概念） | 分割境界 | split 時刻（`bar.time` と同型：`numpy.datetime64`/epoch int）、is_trading_start（IS warmup 後の取引開始・必須）、データ start/end | IsOosInput |
| BacktestStats（既存・無改変） | 1 区間の成績（`models.py` L91-142 で実在） | profit / profit_factor / recovery_factor / expected_payoff / sharpe_ratio / trades / balance_dd_percent ほか | BacktestResult |
| DegradationReport（新規・概念） | OOS vs IS の劣化指標 | **全主要指標について {is_value, oos_value, ratio(OOS/IS), delta(OOS−IS)} を両格納（C-2）**。ratio は IS_x=0 時 None、delta は常時格納 | BacktestStats×2 |
| IsOosResult（新規・概念） | 単純分割の出力一式 | is_stats、oos_stats、degradation | BacktestStats, DegradationReport |

- **抽象度維持**：物理テーブル・クラス名・DDL は内部設計に委譲する（本書では概念定義のみ）。

### 5.3 データフロー図

```
価格データセット(読取専用)
   │  [tools が読み込み・列ブリッジは tmp/新規のみ]
   ▼
全期間バー列 ──slice_is_bars(bars,split) head切り(option b)──► IS 用バー列 ─► request.bars 差替＋controller._interactor.execute ─► IS_stats
   │                                                                                                                              │
   └──── request.bars 無改変＋trading_start=split ───────────► controller._interactor.execute ─► OOS_stats ──────────────────────┤
                                                                                                                                  ▼
                                                                                                  劣化算出(ratio+delta両格納) → DegradationReport
                                                                                                              ▼
                                                                                       並列レポート → 新規出力先(JSON/MD)
```

### 5.4 データライフサイクル

- **入力データ**：読み取り専用（NFR-S1・C1）。改変・移動・削除を行わない。
- **中間バー列**：プロセスメモリ上のみ（永続化しない）。**option b（`request.bars` 差し替え・B-2）採用により IS truncation は in-memory のみで完結し、中間 CSV の永続化は発生しない**。
- **tempfile 規約（M-3・集約）**：列ブリッジ等で一時ファイルが必要な場合に限り、`tempfile`（標準ライブラリ）で生成し**実行後に必ず削除**する。一時ファイルの生成・命名・削除規約は tools 層の単一ユーティリティに集約し（`export_trade_markers.py` L14・L22 `import tempfile` の方針を継承）、複数箇所での tempfile 直接利用を禁止する。
- **出力レポート**：新規パスのみ（§6.5 出力先検証関数で担保・既存生成物を上書きしない）。保持期間・アーカイブは運用判断（TBD・§9.3）。

---

## 6. インターフェース設計

> 出力元：S-3 インターフェース設計

### 6.1 API 設計概要（内部 UC IF・概念レベル）

- **種別**：プロセス内の関数/UC 呼び出し（本サブフェーズに HTTP/REST は含めない。ブラウザ UI は `.doc/ISOOS_BROWSER_PLAN_WIP.md` の Phase 2 で別途）。
- **新規 UC（`simulator/usecase/run_is_oos.py`・概念 IF）**：
  - 入力：`RunIsOosRequest`（概念）= `{ split, is_trading_start（必須・H-1）, 戦略パラメータ群, config_overrides, initial_deposit, symbol 仕様, データ参照 }`。
  - 出力：`IsOosResult`（概念）= `{ is_stats: BacktestStats, oos_stats: BacktestStats, degradation: DegradationReport }`。
- **依存方向の確定（クリーンアーキ遵守）**：
  - `build_interactor`/`run_backtest` は **main 層**にある（`simulator/main/__init__.py`）。usecase が main を import すると依存が外向き（クリーンアーキ違反）になる。
  - **確定方式（H-3 と統合）**：新規 UC は「1 区間を実行する手段」を**呼び出し側（tools 層）からコールバックとして注入**する（依存性注入）。tools 層が `build_interactor(...)` で `(controller, request)` を構築し、**「（full or truncated）bars を受け取り 1 区間を `controller._interactor.execute(request)` で実行して `BacktestStats` を返す」契約のコールバック（run_segment）**を構成して新規 UC へ渡す。新規 UC は UC 層の純関数 `slice_is_bars(bars, split)`（H-3）で IS 用 bars を導出し、run_segment を IS（truncated bars + trading_start=is_trading_start）と OOS（full bars + trading_start=split）の 2 回呼び、劣化指標を算出する。これにより新規 UC は domain のみ依存（main 非依存）を保つ。
    - **呼出経路（B-1・確定）**：run_segment 内部は `controller.run()` ではなく `controller._interactor.execute(request)` を呼ぶ。`controller.run()` は `self._market_data.load(source_ref, ...)`（`adapter/controller.py:50`）で bars を再ロードするため、UC が差し替えた `request.bars`（IS truncation 後）を破棄してしまい truncation が無効化される。`_interactor` は private 属性だが読み取り利用のみで改変なし（NFR-S2/C2 と非矛盾）。先例 `reconcile_is.py:127`・`reconcile.py:127`・`export_trade_markers.py:10` が同経路を実証。
    - 代替案：新規 UC が committed `usecase.RunBacktestInteractor` と `RunBacktestRequest` を直接構築（main を経由しない）。ただし Port 実装（strategy/indicator/tick_model registry）の組み立ては `build_interactor` に集約されており、これを usecase で再実装すると committed ロジックの複製＝重複（DRY 違反）かつ将来の build 仕様変更に追従できない。よってコールバック注入方式を採用する。
    - 出典：公式設計原則（DIP・依存方向内向き）＋（実務的推奨／仮説）。
- **区間スライスの IF 表現（確定・B-2/H-3）**：
  - OOS：`request.bars` 無改変・`trading_start=split`（既存引数・`main/__init__.py` L282・L392 で実証）。
  - IS（**option b に確定**）：`build_interactor` が返した `request.bars` を UC 側で `slice_is_bars(bars, split)`（`bar.time < split` の head 切り）の結果へ差し替え、`trading_start=is_trading_start` を設定して `execute` を呼ぶ。**registry は再構築しない**。根拠：head 切りは保持バーの位置インデックス 0..k-1 を不変に保ち、エンジンの `enumerate(bars)`（`run_backtest.py:203`／every-tick L488）が strategy へ渡す `bar_index` と、full df から事前計算され位置インデックスで保持される指標 registry（`registry.py:25-35`・`update()` は no-op L53）・strategy の `indicators.get(name).iloc[bar_index]` 参照（`tc24051901.py:42`・`pro_fit_band.py:69` 等）が整合するため。option a（IS 用 tmp CSV を `build_interactor(data_path=...)` で渡す＝`market_data.load` L344 で再ロード）は registry を IS 長で再計算するため採らない（旧 TBD-1 を本確定で解消）。

### 6.2 画面構成・遷移

- 該当なし（本サブフェーズは UI を含まない。ブラウザ UI は Phase 2・`.doc/ISOOS_BROWSER_PLAN_WIP.md` §5）。

### 6.3 外部システム連携仕様

| 連携先 | 連携方式 | データ形式 | 頻度 | エラー時動作 |
|---|---|---|---|---|
| committed バックテストエンジン（`simulator.main`/`simulator.usecase`） | プロセス内関数呼び出し（無改変・部品利用） | `RunBacktestRequest` / `BacktestResult`（dataclass） | IS/OOS で 2 回 | エンジンが送出する `ConfigError`/`BacktestError`（`main/__init__.py` L436-439）を UC が捕捉し中断・報告 |
| 価格データセット | ファイル読み取り（読み取り専用） | CSV（comma：`CsvOHLCRepository` / tab：`Mt5CsvOHLCRepository`。`build_interactor` L312-337 で ea_name により分岐） | 1 回ロード | 読込失敗は `DataError`（`main/__init__.py` L166-174）で中断 |

### 6.4 通信プロトコル・データ形式

- **プロトコル**：なし（プロセス内）。
- **出力データ形式（L-1・確定）**：
  - 機械可読：JSON（IS_stats / OOS_stats / degradation）。`asdict(result.stats)`（`main/__init__.py` L464 の既存パターン）で `BacktestStats` を dict 化可能。
  - 人間可読：Markdown 並列レポート（IS 列｜OOS 列｜劣化列）。
  - **整形は tools 層内で行い、新規 presenter を追加しない（L-1）**。committed presenter（`JsonPresenter`/`MarkdownPresenter`）は改変も流用もせず、tools 層が `asdict` 済 dict を JSON シリアライズ＋Markdown 表に整形する（adapter 層に presenter を増やさず C2 無改変・新規ファイル最小を維持）。
  - 出典：プロジェクト規約（既存 presenter 形式に整合・tools 層内整形）。

### 6.5 出力先検証（データ非波及の機構・H-2）

- **検証関数（新規・概念）**：書き込み先パスを引数に取り、「指定 OUT ディレクトリ配下のみ許可」を判定する純関数を tools 層に設ける。
- **拒否条件**：解決済み絶対パスが `marketdata/`・`simulator/tests/fixtures/`・`simulator/tests/confirmation/` のいずれかのプレフィクスに該当する書き込みを**拒否**する（C1/NFR-S1）。許可されるのは引数で渡された新規 OUT ディレクトリ配下のみ。
- **検証手段（計測可能・H-2）**：NFR-S1 は (1) 上記検証関数の単体テスト（禁止プレフィクスへの書込みが拒否されること）、(2) 結合テストで「実行前後に既存データディレクトリ配下ファイルの mtime が不変であること」を assert、の 2 点で実証する。先例 `export_trade_markers.py` L14（tempfile 即時削除・新規 OUT のみ）の C1 パターンを検証関数として明文化する。

---

## 7. 非機能設計

> 出力元：S-4 品質特性の担保

### 7.1 性能設計・スケーラビリティ対策

| 要件 | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| NFR-P1 エンジン実行回数 | 2 回（IS+OOS） | 最適化なしのため param 探索ループは持たない。IS/OOS は重複しない区間のため総処理バー数は単一フル run と同程度（OOS の warmup として IS 区間が再走する分のみ追加。`run_backtest.py` L208-209 の warmup は指標 update のみで約定処理を行わずトレード列を生成しないため低コスト） | run 全体の wall-clock（先例 `reconcile.py` L137 が `dt` を計測） |
| NFR-P2 劣化算出コスト | `BacktestStats` フィールド数（数十）の O(1) 算術 | `BacktestStats` 上の比・差分のみ。バー数 N に非依存 | — |
| スケーラビリティ | 後段②③で N 回 run へ拡張時に非同期ジョブ化（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §4） | 本サブフェーズは同期実行で可（単純分割は 2 run・同期応答で十分） | — |

### 7.2 可用性設計・障害対策

| 要件 | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| NFR-A1 実行完了率 | バッチ実行のため SLA/稼働率対象外（オフライン分析ツール） | エンジンの例外（`ConfigError`/`BacktestError`/`MarginCallError`）を UC が捕捉し、IS/OOS いずれかの失敗を明示して中断（部分結果の黙殺をしない） | 単体テストで例外伝播を検証 |

- 本機能はオフラインの分析ツールであり、常時稼働サービスの可用性（99.x%・MTTR）は適用対象外（該当なし）。

### 7.3 セキュリティ設計

- **認証・認可**：該当なし（プロセス内ローカル実行・本サブフェーズに公開エンドポイントなし）。
- **入力検証**：split・is_trading_start の時刻型・範囲（`start <= is_trading_start <= split <= end`）と空区間判定式（M-1：IS バー数 ≥ 1 かつ OOS で `bar.time >= split` のバー数 ≥ 1）を UC が事前検証する（F-02/F-03 例外条件）。データパスは既存 tools の datasetRef ホワイトリスト方式に準拠可能（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §1。Phase 2 の HTTP 化時に必須）。
- **データ保護（最重要・C1/NFR-S1）**：既存データ（`marketdata/`・`fixtures/`・`confirmation/`）への書き込みを**§6.5 の出力先検証関数で構造的に禁止する**（拒否プレフィクスを明示）。読み取りは read-only オープン、書き込みは検証関数を通過した新規 OUT 配下のみ。一時生成は §5.4 の tempfile 規約（実行後削除）に従う。NFR-S1 は §6.5 の計測可能手段（検証関数の単体テスト＋既存ディレクトリの mtime 不変 assert）で実証する（H-2）。
- **監査ログ**：該当なし（オフラインツール。実行ログは標準出力で足りる）。
- 詳細脅威分析は security スキル成果物を参照（本書では扱わない）。

### 7.4 運用・保守性設計

- **ログ設計**：標準出力に IS/OOS の trades・net・balance・劣化指標を出力（先例 `reconcile.py` L136-177 のレポート形式を踏襲）。
- **監視ポイント**：該当なし（オフライン）。
- **構成管理**：新規ファイルのみ追加（`simulator/usecase/run_is_oos.py`・`simulator/tools/run_is_oos_cli.py`・必要時 presenter）。既存ファイルへの差分 0（NFR-S2）。
- **バックアップ・リストア**：該当なし（出力は再生成可能・入力は読み取り専用）。
- **保守性（拡張点）**：劣化指標の対象指標集合・出力フォーマットを設定外部化し、後段②の目的関数 Port が同じ `BacktestStats` 抽出ロジックを再利用できるよう「指標抽出」を独立関数に切り出す（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §3 の `ObjectivePort` の前身）。

---

## 8. 開発・運用方針

> 出力元：S-4 品質特性の担保

### 8.1 開発方法論・プロセス

- `.claude/CLAUDE.md` のフロー（分析→計画→実行→品質管理→報告）に準拠。指示範囲（単純分割のみ）に限定し、最適化/WF の先行実装を行わない。

### 8.2 品質保証方針

- committed エンジンの bit-exact 突合資産（Phase 0）を無波及で保護（NFR-S2）。新規 UC は committed の振る舞いを変えないことを単体テストで担保する。
- **保証境界（C-1）**：バイト同一の結合テスト実証は `pending_lifecycle` 経路（StopEntryProbe・every-tick・`run_backtest.py` L157-160/L488）のみ。なお StopEntryProbe は registry を未参照（`main/__init__.py` L327）のため、結合突合の byte-exact は bars＋tick 処理に由来し registry 整合は無関係。一方 bar-mode 経路（L203）の TC24051901/MaSlope は `.iloc[bar_index]` で registry を参照するため、**`slice_is_bars` 後の `request.bars` 差し替えが位置インデックス整合（causal EMA により registry 非再構築でも値 bit-identical）を保つことを単体テストで担保**する。両経路の役割分担が C-1 の限定根拠。
- 回帰テスト方針（user memory「bugfix-pair-with-regression-test」に整合）：IS/OOS スライスの境界バグ（head 切り境界 `< split` ・位置インデックスずれ）を禁止する回帰テストを 1 本添える。

### 8.3 テスト方針

- **単体（UC）**：(1) `slice_is_bars` の境界（`< split` 含む head 区間／`>= split` 除外）が正しいこと、(2) **全主要指標について ratio・delta の両方が `BacktestStats` から正しく算出されること（C-2）**、(3) ゼロ除算（IS_x=0）が `ratio=None`・`delta` 格納として扱われること、(4) 出力先検証関数が禁止プレフィクスを拒否すること（H-2）、(5) bar-mode 経路で `request.bars` 差し替え後も位置インデックス整合（registry 非再構築）が保たれること（C-1）。
- **結合（先例突合）**：Phase 0 の 2026-04 データで、本 UC 経由（`controller._interactor.execute` 直接呼び・B-1）の IS_stats/OOS_stats が先例 `reconcile_is.py`（IS net +11370・balance 21370）／`reconcile.py`（OOS net -4020・balance 5980）の結果と一致すること（両先例が固定値を docstring・実装に保持・`reconcile_is.py:127`/`reconcile.py:127` で同経路）。この結合突合は `pending_lifecycle` 経路の保証境界（C-1）を実証する。加えて既存データディレクトリの mtime 不変 assert で NFR-S1 を実証（H-2）。
- **テスト戦略概要**：合成小データ（`marketdata` 実データ非依存・`main/__init__.py` L89-91 の方針）でスライスロジックを検証し、Phase 0 データで end-to-end 整合を確認。

### 8.4 リリース・デプロイメント方針

- 環境構成：開発＝ローカル（本サブフェーズに staging/本番なし）。
- デプロイ戦略：該当なし（ライブラリ/ツールとしてリポジトリに追加）。

---

## 9. リスク・課題

> 出力元：S-1 リスク列挙 ／ S-5 設計検証

### 9.1 技術的リスクと対策

| リスク | 影響度 | 発生確率 | 対策 | 対策の出典／根拠 |
|---|---|---|---|---|
| R-1 IS 終端を committed IF で表現できない（`trading_end` 不在） | 高 | 確 | option b（B-2）：`build_interactor` が返した `request.bars` を `slice_is_bars(bars, split)`（`bar.time < split` head 切り）の結果へ in-memory 差し替え＋`controller._interactor.execute` 直接呼び（B-1）。registry 非再構築（位置インデックス整合） | `build_interactor` シグネチャ L256-285（終端引数なし）・`controller.py:50`（run は再ロード）・`run_backtest.py:203`（enumerate）・`registry.py:53`（update no-op）・`reconcile_is.py:127`（実証） |
| R-2 split 時刻型と `bar.time` 型の不整合 | 中 | 中 | UC 入口で split を `bar.time` と同型へ正規化（`numpy.datetime64`/epoch int）。pandas 依存は tools 層に閉じ usecase へ漏らさない | `run_backtest.py` L82-84・`reconcile.py` L125（実証） |
| R-3 OOS warmup（split 前バー）不足で指標が seed 収束しない | 中 | 低 | OOS は全期間バー列をそのまま入力し、split 前を warmup として走らせる（trading_start により約定除外） | `run_backtest.py` L207-209（実証） |
| R-4 committed エンジンへの意図せぬ波及 | 高 | 低 | 新規ファイルのみ追加し既存ファイル差分 0 を CI/差分確認で担保（NFR-S2） | プロジェクト規約 C2 |
| R-5 既存データへの書き込み波及 | 高 | 低 | 読み取り専用オープン＋新規出力先のみ＋tempfile 即時削除 | C1・`export_trade_markers.py` L14（先例） |

### 9.2 スケジュール・リソースリスク

- 本サブフェーズは新規 2〜3 ファイル（UC・tools・任意 presenter）に限定され、committed 改修を伴わないため波及リスクは限定的。後段②③の前提依存（本 UC の IF 安定性）が主リスク。

### 9.3 今後の検討課題（TBD 一覧）

| 項目 | 確認が必要な理由 | 確認先／確認方法 |
|---|---|---|
| ~~TBD-1：IS truncation の実装手段~~（**解消済み・B-2**） | option b（`request.bars` 差し替え・registry 非再構築）に確定。head 切りが位置インデックス 0..k-1 を不変に保ち full df registry（`registry.py:25-35`・update no-op）・strategy の `.iloc[bar_index]`（`run_backtest.py:203` enumerate）と整合することを実コードで実証済 | 解消済み（§6.1・F-02/F-03 区間スライス方式に反映） |
| ~~TBD-2：劣化指標の「比 vs 差分」の指標別使い分け~~（**解消済み・C-2**） | 指標別の使い分けは行わず、**全主要指標に ratio・delta を両格納し解釈を利用側に委ねる（提示のみ）**に確定。対象は `BacktestStats`（`models.py` L98-105）の profit/profit_factor/recovery_factor/expected_payoff/sharpe_ratio/trades | 解消済み（F-05・§5.2 DegradationReport に反映） |
| TBD-3：出力レポートの新規パス規約 | 既存生成物を上書きしない出力先の命名規約（§6.5 検証関数の許可ディレクトリの具体パス） | 運用方針として確定（Phase 2 のブラウザ応答形式とも整合） |
| ~~TBD-4：IS 開始時刻の指定方法~~（**解消済み・H-1**） | `RunIsOosRequest` に `is_trading_start`（必須）を確定。区間定義（データ先頭〜is_trading_start＝IS warmup／is_trading_start〜split＝IS 取引／split〜end＝OOS 取引／データ先頭〜split＝OOS warmup）を明文化 | 解消済み（リスク-1・§5.2・§6.1 に反映） |
| TBD-5：時刻正規化の責務と方式（L-2） | split・is_trading_start を `bar.time`（`numpy.datetime64`/epoch int）と同型へ正規化する処理を tools 層・UC 層のどちらに置くか、`pd.Timestamp` 入力をどう受けるか（先例 `reconcile.py:125` は `pd.Timestamp` を `trading_start` に直接渡す）。pandas 依存を usecase へ漏らさない制約との整合 | TBD として明示（実装着手時に tools 層正規化＋UC は比較可能型受領を前提とする方針で確定予定） |

---

## 10. 付録

### 10.1 用語集

| 用語 | 定義 |
|---|---|
| IS（In-Sample） | 学習/評価対象の区間 `[start, split)`。本サブフェーズでは最適化せず固定パラメータを評価する |
| OOS（Out-of-Sample） | 検証区間 `[split, end)`。IS と同一パラメータで評価し堅牢性を測る |
| split | IS と OOS を分ける境界日時（半開区間：split は OOS 側に属する） |
| warmup | 指標 seed 収束のため取引開始前に走らせるバー区間（`trading_start` 未満。約定・stats 除外） |
| 劣化指標 | OOS の成績が IS に対しどれだけ劣るかの比（OOS/IS）・差分（OOS−IS） |
| committed エンジン | MT5 bit-exact 突合済の既存バックテストエンジン（`simulator/`・無改変対象） |
| BacktestStats | 1 区間の成績 DTO（`simulator/usecase/models.py` L91-142） |

### 10.2 設計判断の根拠・トレードオフ

| 判断項目 | 採用 | 代替 | 根拠 | 出典区分 |
|---|---|---|---|---|
| アーキテクチャ | 新規オーケストレーション UC（委譲・合成） | committed `execute` 改修 / tools 直書き | C2 無改変・後段土台化を両立 | 仮説＋規約 |
| 呼出経路（B-1） | `controller._interactor.execute(request)` 直接呼び | `controller.run()` / `run_backtest()` | `controller.run` は `data_path` から再ロード（`controller.py:50`）し差し替えた `request.bars` を破棄＝truncation 無効化 | 公式（実証）＋先例（`reconcile_is.py:127`） |
| OOS スライス | `request.bars` 無改変＋`trading_start=split` | データ start を split に再ロード | 既存引数で表現可・warmup 確保 | 公式（既存 IF 実証） |
| IS スライス（B-2） | option b：`request.bars` を `slice_is_bars`（head 切り）へ差し替え・registry 非再構築 | option a：IS 専用 tmp CSV を `build_interactor(data_path=...)` で再ロード | head 切りが位置インデックス 0..k-1 を不変に保ち full df registry（update no-op・`registry.py:53`）・`.iloc[bar_index]`（enumerate・`run_backtest.py:203`）と整合 | 公式（実証）＋仮説 |
| truncation 責務層（H-3） | UC 層純関数 `slice_is_bars(bars, split)` | エンジン内 / tools 内 | 副作用なし・domain のみ依存・コールバックは「（full or truncated）bars を受けて 1 区間 execute」契約に統一 | 公式（純関数・関心分離） |
| IS 開始指定（H-1） | `RunIsOosRequest.is_trading_start`（必須） | split を IS 開始に流用 | 先例は IS/OOS で別 trading_start（`reconcile_is.py:36`/`reconcile.py:36`）。区間定義を明文化 | 公式（実証） |
| 依存方向 | tools が区間実行コールバック（run_segment）を注入 | UC が main を import | usecase→domain のみの内向き依存を保持 | 公式（DIP） |
| 劣化指標（C-2） | 全主要指標に ratio・delta を両格納（提示のみ） | 指標別に比 or 差分を使い分け | 解釈を利用側に委ね TBD-2 の使い分け不確定性を排除 | 仮説＋規約 |
| 劣化合否判定 | 提示のみ（合否なし） | 閾値で合否判定 | 単純分割段は最適化なし。合否は後段②目的関数 | 仮説 |
| 出力形式（L-1） | JSON＋Markdown 並列・tools 層内整形（新規 presenter なし） | 新規 presenter 追加 / JSON のみ | 機械可読＋人間可読の両立・adapter 無改変・新規ファイル最小 | 規約 |
| データ非波及（H-2） | 出力先検証関数＋mtime 不変 assert | コメント上の方針のみ | 計測可能手段で NFR-S1 を実証 | 規約＋公式（検証可能性） |

### 10.3 参考資料

- `simulator/main/__init__.py`（`build_interactor` L256-394 / `run_backtest` / `compare_stats`。`compare_run` は削除済み・2026-07-18）
- `simulator/adapter/controller.py`（`BacktestController.run` L37-64＝`market_data.load` 再ロード L50 ／ `_interactor` private 属性 L35 ＝B-1 根拠）
- `simulator/usecase/run_backtest.py`（`RunBacktestRequest` / `trading_start` warmup 機構 L79-84/L208-209 / bar-mode `enumerate(bars)` L203 / every-tick L488）
- `simulator/adapter/indicator/registry.py`（`PandasIndicatorRegistry`＝full df 事前計算系列・位置インデックス保持・`update()` no-op L53＝B-2 根拠）
- `simulator/adapter/strategy/tc24051901.py`・`pro_fit_band.py`（`indicators.get(name).iloc[bar_index]` 参照＝位置インデックス整合の根拠）
- `simulator/usecase/models.py`（`BacktestResult` / `BacktestStats` L91-142 / `SymbolSpec`）
- `simulator/tests/confirmation/2026-04_stop-probe_oos/reconcile.py`（OOS 分割先例）
- `simulator/tests/confirmation/2026-04_stop-probe_oos/reconcile_is.py`（IS 分割先例）
- `simulator/tools/export_trade_markers.py`（tools 層・C1/C3 パターン先例）
- `.doc/ISOOS_BROWSER_PLAN_WIP.md`（IS/OOS 全体計画・Phase 1）

### 10.4 関連する標準・規格

- クリーンアーキテクチャ（依存方向の内向き規律・DIP）：本リポジトリの既存規約（`run_backtest.py` L7-8・`CLEAN_ARCH` 参照）。
- DRY / YAGNI（公式設計原則）：committed build ロジックの複製回避・単純分割段での合否ロジック非導入。

---

## 後段サブフェーズへの拡張余地（本設計が土台になること）

- **②最適化（`usecase/optimize.py`）**：本 UC の「IS run → stats 抽出」を、`ParameterSearchPort` がパラメータ空間を走査して IS で繰り返し呼ぶプリミティブとして再利用する。F-05 の「指標抽出」を独立化しておくことで `ObjectivePort` の目的関数がそのまま再利用できる（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §3）。
- **③ウォークフォワード（`usecase/walk_forward.py`）**：本 UC の「split による IS/OOS スライス＋区間 run」を、anchored/rolling 窓ごとに反復（split を窓境界として複数回適用）する基本単位として再利用する。IS truncation（R-1 対策）と OOS warmup（R-3 対策）の機構が窓ごとにそのまま適用できる。
- いずれも committed エンジンは無改変のまま、新規 UC の「上」にさらに UC を重ねる構造（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §3 のレイヤリング図）に整合する。
