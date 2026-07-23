# BTLM 当日値トラッキング指標 基本設計書

> 対象機能: `tgp_btlm` の OLS 回帰（`OlsBtlmFitter`）が算出する当日値（`btlm_mean` /
> `btlm_q5` / `btlm_q95`）を時系列で連結し、ドット（サークル）で描画する新規オーバーレイ
> 指標。連結系列を `moving_averages` の移動平均で平準化する機能を持つ。
> ステータス: 基本設計（レビュー前・実装未着手）。
> 作業ワーキング指標 ID: `btlm_track`（最終名称は §9.3 TBD-07 で確定）。

---

## 1. 文書情報

- 作成日：2026-07-19
- バージョン：v0.1.0（基本設計・初版）
- 作成者：system-basic-design エージェント
- 承認者：（未承認）
- 変更履歴：
  | 版 | 日付 | 変更内容 |
  |---|---|---|
  | v0.1.0 | 2026-07-19 | 初版作成（要件からの基本設計） |

## 2. プロジェクト概要

### 2.1 システム概要

- **位置付け**：既存指標基盤（`indigators/indicator_ui/`・`indigators/tgp_btlm/`・
  `indigators/moving_averages/`）の上に追加する新規オーバーレイ指標。既存指標を
  改変せず参照のみで拡張する（OCP・プロジェクト規約「主機能無改変の参照拡張」より）。
- **解決する課題**：現行 `tgp_btlm` は「直近 1 窓（`min(maxbars, n)` 本）の回帰当てはめ
  曲線」を描画する（実測: `indigators/tgp_btlm/src/bands.py:72-95`）。当てはめ窓の各位置
  1..window の予測値を全て返すため、窓を跨いだ「各バー時点での当日推定」の推移は表現され
  ない。本指標は各バー時点で回帰を当てはめ直し、その**当日値のみ**を時系列で連結して、
  推定トレンドと予測分位の時間推移を可視化する。

### 2.2 開発目的・背景

- **背景**：ユーザー要件（原文）に基づく。ベースは `tgp_btlm` の `ols`
  （`OlsBtlmFitter`・`indigators/tgp_btlm/src/reference.py:30-69`）。
- **達成目標**：
  1. 動的窓 `maxbars` で当てはめた OLS の当日値 `btlm_mean` / `btlm_q5` / `btlm_q95`
     をドット（サークル）で連結描画する。
  2. 連結系列を `moving_averages` の移動平均で平準化する（表示オン/オフ切替可能）。
  3. 移動平均の種別・ソース選択肢を `moving_averages` と同期する。

### 2.3 適用範囲・制約条件

#### 機能要件サマリー（要件 ID 一覧）

| 要件 ID | 要件 | 出所 |
|---|---|---|
| FR-01 | OLS 当日値 3 系列（`btlm_mean`/`btlm_q5`/`btlm_q95`）を時系列連結し描画する | ユーザー要件 |
| FR-02 | 既定描画形式はドット（サークル）。ライン描画も選択可能 | ユーザー要件 |
| FR-03 | 連結系列を移動平均で平準化する | ユーザー要件 |
| FR-04 | 移動平均の表示をオン/オフ切替する | ユーザー要件 |
| FR-05 | 移動平均の種別を `moving_averages` の MA 種別選択肢と同一にする | ユーザー要件 |
| FR-06 | 計算ソース（旧 `price`）を「ソース」に改名し `moving_averages` のソース選択肢と同期する | ユーザー要件 |
| FR-07 | 窓幅 `maxbars` をパラメータとして持つ（動的窓） | ユーザー要件 |

#### 非機能要件サマリー（数値目標）

- ユーザーからの数値目標の提示は**なし**。本設計では性能・応答の数値目標を仮説として提示し、
  実測による確定を §9.3 TBD-08 に委ねる（プロジェクト規約「実証なき憶測禁止」より）。

#### 制約条件

| 区分 | 制約 | 出典 |
|---|---|---|
| 技術（不変） | `tgp_btlm`・`moving_averages`・`common/applied_price` を**改変しない**（参照のみ） | プロジェクト規約（OCP） |
| 技術（不変） | 新規依存を追加しない（numpy / pandas / stdlib のみ）。既存指標基盤の技術構成に従う | プロジェクト規約 |
| 技術（不変） | 分位点は既定 `q_low=0.05` / `q_high=0.95` 固定（系列名 `btlm_q5`/`btlm_q95` が要件で明示） | ユーザー要件（系列名指定） |
| 技術（不変） | fitter は `ols`（`OlsBtlmFitter`）固定。`tgp`（R/tgp/rpy2）は対象外 | ユーザー要件「ベースは ols」 |
| 運用 | 描画は既存 lightweight-charts v5 基盤（オーバーレイ pane） | 既存基盤 |

#### 設計上の課題と技術的リスク

| # | 課題／リスク | 内容 |
|---|---|---|
| C-1 | 「当日値」の定義が参照実装に非明示 | `bands.py` は窓全体の値を返し、当日値の抽出コードは存在しない（§9.3 TBD-01） |
| C-2 | 走査再計算コスト | 各バーで OLS 当てはめを再計算すると O(N·maxbars)。N 大で性能懸念（§7.1） |
| C-3 | 合成ソースの注入 | `bands.py:67-69` は実在列名のみ受理。合成ソース（hl2 等）は列注入が必要（§4.2） |
| C-4 | 平準化 MA の適用対象・パラメータが要件に非明示 | 全 3 系列か平均のみか、期間の既定値が未定（§9.3 TBD-03/04） |

## 3. システムアーキテクチャ

### 3.1 全体構成図

```
[既] OHLC DataFrame（昇順・open/high/low/close/time）
        │
        ▼
★[adapter] SourcePriceResolver           ← source を common.applied_price で 1 次元価格列へ合成
        │   （close/open/high/low/hl2/hlc3/ohlc4/hlcc4）
        ▼
★[usecase] DailyTrackComputer             ← 各バー t で直近 maxbars 本に対し OLS を当てはめ、
        │   （tgp_btlm.reference.OlsBtlmFitter を参照）  当日値 mean[-1]/q_low[-1]/q_high[-1] を収集
        ▼
   連結系列 3 本（btlm_mean / btlm_q5 / btlm_q95・時系列）
        │
        ├───────────────► ★[adapter] 描画（ドット既定／ライン選択）
        │
        ▼（表示 ON 時のみ）
★[usecase] Smoother                       ← moving_averages.core の MA 関数を参照して平準化
        │   （sma/ema/smma/lwma・種別は同期）
        ▼
   平滑系列（表示 ON 時のみ）───────────► ★[adapter] 描画（MA ライン）
```

★ = 新規追加要素。既存要素（`tgp_btlm`・`moving_averages`・`applied_price`）は無改変で参照する。

### 3.2 アーキテクチャパターン選択理由

- **採用**：レイヤード（core / usecase / adapter）＋ ポート境界による参照拡張。
  既存 `indigators/*/src/`（core=純粋ロジック、bands/lwc_chart=成果物・描画アダプタ）の
  層構造に対称化する（プロジェクト規約「主機能と対称に」より）。
- **比較した代替案**：

  | 案 | 概要 | 採用可否 | 理由 |
  |---|---|---|---|
  | A. 参照拡張（新規モジュール・既存を import） | 新指標を独立モジュールとし `tgp_btlm`/`moving_averages` を import 参照 | **採用** | 既存無改変（OCP）を満たす。既存 4 指標（tgp_btlm/profit_band/price_range_power/moving_averages）と同じ登録方式で UI 統合可能 |
  | B. `tgp_btlm` 拡張（既存 bands.py に当日値モード追加） | `build_btlm_bands` に走査モードを追加 | 棄却 | 既存 `tgp_btlm` を改変する。共有モジュール破壊のリスク（プロジェクト規約禁止事項） |
  | C. front（JS）側のみで連結・平滑 | 既存 tgp_btlm 出力を front で再合成 | 棄却 | tgp_btlm は 1 窓しか計算せず、当日値の走査履歴を持たない。front では走査再計算の入力が得られない |

- **出典区分**：（実務的推奨／仮説）。ただし「既存無改変」制約は プロジェクト規約（絶対遵守）。

### 3.3 技術スタック詳細

| 層 | 採用技術 | バージョン | 代替候補 | 採用根拠 |
|---|---|---|---|---|
| 計算（core/usecase） | Python + numpy | 既存構成に一致（無断変更禁止） | pandas 全面 | `OlsBtlmFitter` が numpy 実装（reference.py:25）。既存 core 層は numpy のみ（プロジェクト規約） |
| 成果物整形 | pandas | 既存構成に一致 | numpy のみ | `bands.py` が pandas.DataFrame を返す（bands.py:88-95）。既存アダプタと整合 |
| ソース合成 | `common.applied_price` | 既存共有層 | 自前実装 | 合成価格の単一定義（applied_price.py:1-23）。`moving_averages` も委譲（lwc_chart.py:25,121） |
| MA 計算 | `moving_averages.core` | 既存共有層 | 自前 MA | MA 種別同期の単一情報源（README.md・core.py 4 種） |
| 描画 | lightweight-charts v5（既存同梱） | 既存基盤 | 追加ライブラリ | 新規依存禁止（制約） |

### 3.4 レイヤー構成・責務分担

| レイヤー | 責務 | 依存先 | 出典／根拠 |
|---|---|---|---|
| core（純粋ロジック） | 当日値抽出規則・走査窓の定義（外部 I/O 非依存） | numpy のみ | 既存 core 層規約（core.py:1-23） |
| usecase | 走査再計算オーケストレーション（OLS 当てはめ反復・MA 平滑呼出） | `tgp_btlm.reference` / `tgp_btlm.bands` / `moving_averages.core` を参照 | 参照拡張（OCP） |
| adapter（成果物・描画） | ソース合成・系列 DataFrame 整形・lwc への系列追加（ドット/ライン切替） | usecase + `common.applied_price` + pandas | 既存 lwc_chart アダプタと対称（lwc_chart.py:1-13） |
| front / catalog | パラメータ定義・UI メタ・系列名照合（F3） | 既存 `usecase/catalog.js` レジストリ | 既存 4 指標と同一登録方式（catalog.js:69-250） |

- 依存方向は core ← usecase ← adapter ← front の一方向（循環なし）。ドメイン層が UI に依存しない。

## 4. 機能設計

### 4.1 機能一覧・優先度

| 機能 ID | 機能名 | 概要 | 優先度 | 対応要件 ID |
|---|---|---|---|---|
| F-01 | 当日値走査計算 | 各バー t で直近 `maxbars` 本に OLS 当てはめ→当日値収集 | 高 | FR-01, FR-07 |
| F-02 | 3 系列連結描画 | `btlm_mean`/`btlm_q5`/`btlm_q95` を時系列連結 | 高 | FR-01 |
| F-03 | 描画形式切替 | ドット（サークル・既定）／ライン | 高 | FR-02 |
| F-04 | ソース合成 | 「ソース」選択→ OHLC 合成価格列を回帰対象に | 高 | FR-06 |
| F-05 | MA 平準化 | 連結系列を MA で平滑 | 中 | FR-03 |
| F-06 | MA 表示切替 | MA 表示オン/オフ | 中 | FR-04 |
| F-07 | MA 種別同期 | MA 種別を `moving_averages` と同一選択肢 | 中 | FR-05 |

### 4.2 機能詳細仕様（主要機能）

#### F-01 当日値走査計算

- **入力**：昇順 OHLC DataFrame、`maxbars`（既定 100・実測 `core.py:33 DEFAULT_MAXBARS=100`）、
  合成済みソース価格列、`q_low=0.05`/`q_high=0.95`（実測 `core.py:34-35`）。
- **処理（設計方針・要 §9.3 TBD-01/02 確定）**：
  1. ソース価格系列 `s[0..N-1]`（合成済み）を得る。
  2. 各バー `t`（`t = t0 .. N-1`、`t0` は最小観測数を満たす開始点）について、直近窓
     `s[t-window+1 .. t]`（`window = min(maxbars, t+1)`）を対象に OLS を当てはめる
     （`OlsBtlmFitter.fit_predict`・reference.py:33-69）。
  3. 当てはめ結果のうち**最新位置（窓末尾）の値** `mean[-1]` / `q_low[-1]` / `q_high[-1]`
     を、バー `t` の当日値として収集する。
  4. `t` を進めて時系列 3 本を得る。窓不足（観測 < 3・reference.py:49）の先頭区間は NaN。
- **参照実装との関係**：`build_btlm_bands`（bands.py:34-95）は 1 窓に対し窓全体の値を返す。
  本機能は「当日値（窓末尾値）のみ」を各 `t` で収集する走査計算であり、`bands.py` に無い
  合成である（§9.3 TBD-01/02）。`bands.py` を再利用する場合は各 `t` で
  `build_btlm_bands(df.iloc[:t+1], OlsBtlmFitter(), price=..., maxbars=...)` を呼び、
  返り値の最終行を取る（既存無改変）。
- **後条件**：3 系列は入力 index を引き継ぎ、各バーに 0/1 点。非因果参照なし（各 `t` の値は
  `t` までのデータのみに依存＝過去バーは再計算で不変・非 repaint）。
- **例外**：ソース列欠落 → `KeyError`（bands.py:68-69 準拠）。`q_low<q_high` 違反 →
  `ValueError`（bands.py:61-62 準拠）。空系列 → `ValueError`。

#### F-04 ソース合成（旧 `price` → 「ソース」）

- **選択肢（`moving_averages` と同期）**：`close`/`open`/`high`/`low`/`hl2`/`hlc3`/`ohlc4`/`hlcc4`
  （実測 `catalog.js:230`・`MA_SOURCE_LABELS` catalog.js:207-211）。
- **合成**：`common.applied_price.applied_price(kind, o, h, l, c)`（applied_price.py:109-150）へ
  委譲。写像は `moving_averages` の `_SOURCE_TO_APPLIED`（lwc_chart.py:37-46）と同一：
  hl2→MEDIAN / hlc3→TYPICAL / hlcc4→WEIGHTED / ohlc4→OHLC4。
- **注入（C-3 対応）**：`bands.py:67-69` は実在列名のみ受理するため、合成価格（hl2 等）は
  新指標 adapter が一時列として DataFrame に付与し、その列名を `price` 引数へ渡す
  （`tgp_btlm` 無改変・参照拡張）。
- **注記**：現行 `tgp_btlm` の `price` 選択肢は 4 種のみ（`['open','high','low','close']`・
  catalog.js:79）。本要件はこれを 8 種へ拡張して `moving_averages` と同期する（新指標側のみ・
  既存 `tgp_btlm` の選択肢は改変しない）。

#### F-05／F-07 MA 平準化・種別同期

- **種別（`moving_averages` と同期）**：`sma`/`ema`/`smma`/`lwma`（実測 `catalog.js:228`・
  `MA_TYPE_LABELS` catalog.js:206・`moving_averages/src/core.py` 4 関数）。
- **計算**：`moving_averages.core` のバッファ関数
  （`simple_ma_on_buffer`/`exponential_ma_on_buffer`/`smoothed_ma_on_buffer`/
  `linear_weighted_ma_on_buffer`・README API 表）を参照して連結系列へ適用する。
  warm-up マスクは `moving_averages` の慣行（lwc_chart.py:56-57,130-133）に対称化する。
- **適用対象・期間**：要件に非明示（§9.3 TBD-03/04）。設計案は「3 系列それぞれに同一 MA を
  適用・期間既定 9（`moving_averages` length 既定 9・catalog.js:229 / catalog_schema.py:59）」だが
  確定要。
- **表示切替（FR-04）**：MA 系列の出力有無を BOOL パラメータで制御。OFF 時は MA 系列を出さない
  （`moving_averages` が `smoothing_type=='none'` で Smoothing 系列を出さない挙動・
  lwc_chart.py:229 に対称）。

### 4.3 処理フロー図

```
UI パラメータ（maxbars/source/display_mode/ma_show/ma_type/ma_length …）
      │
      ▼
compute 要求 → adapter: source 合成（applied_price）
      │
      ▼
usecase: for t in [t0..N-1]:  OLS 当てはめ（直近 maxbars）→ 当日値 3 点収集
      │
      ▼
連結 3 系列 → （display_mode）→ ドット or ライン系列を生成
      │
      ▼（ma_show=true）
usecase: MA 平滑（ma_type/ma_length）→ MA ライン系列を生成
      │
      ▼
adapter: lwc へ系列追加（F3 系列名照合）→ 描画
```

### 4.4 業務フロー・ユースケース

- UC: ユーザーが指標一覧から本指標を選択 → パラメータダイアログで
  `maxbars`/ソース/表示形式/MA 設定を調整 → チャートにドット（又はライン）＋任意で MA を重畳。
- 既存指標管理 UI（`.doc/indicator-management-ui/基本設計書.md`）のダイアログ機構を流用する
  （新規 UI 機構は追加しない）。

## 5. データ設計

### 5.1 データモデル概要

- 本指標は DB・永続状態を持たない純粋計算（入力 OHLC 配列 → 出力系列）。
  `.doc/indicator-management-ui/INDICATOR_CALC_MODEL.md`（Item7 入力配列限定型）に準拠。

### 5.2 主要エンティティ定義（概念）

| エンティティ | 概要 | 主要属性 | 関連 |
|---|---|---|---|
| ソース価格系列 | 合成後 1 次元価格列 | 種別（8 択）、値配列（昇順） | OHLC 入力から生成 |
| 当日値系列（連結） | バーごとの窓末尾 OLS 予測 | `btlm_mean`/`btlm_q5`/`btlm_q95`、time、value | ソース価格系列に依存 |
| 平滑系列 | 当日値系列の MA | ma_type、length、value | 当日値系列に依存（表示 ON 時のみ） |

- 成果物列名は `tgp_btlm` の命名規則を継承：`btlm_mean`（core.py:40）、
  `btlm_q{int(round(q*100))}` → `btlm_q5`/`btlm_q95`（core.py:48-50）。系列名は F3 照合対象。

### 5.3 データフロー図

- §3.1 全体構成図に同じ（OHLC → 合成 → 走査 OLS → 連結 → 平滑 → 描画）。

### 5.4 データライフサイクル

- 揮発（計算のたびに再生成）。保持・アーカイブ・削除ポリシーは持たない（純粋計算）。

## 6. インターフェース設計

### 6.1 API 設計概要

- 既存 compute 経路（`indigators/indicator_ui/api/`・`GET /catalog`＋ compute 呼出）へ
  新規 `compute_id`（ワーキング `btlm_track`）を登録して統合する（既存 19 バインディングと
  同一機構・catalog_schema.py:12-14）。新規エンドポイントは追加しない。

#### パラメータ一覧（設計案）

| パラメータ | 型 | 既定 | 選択肢／制約 | 出所・実測根拠 |
|---|---|---|---|---|
| `source`（旧 `price`） | ENUM | `close`（TBD-05） | close/open/high/low/hl2/hlc3/ohlc4/hlcc4 | `moving_averages` と同期（catalog.js:230） |
| `maxbars` | INT | 100 | ≥ 1 | tgp_btlm（core.py:33・catalog.js:81） |
| `display_mode` | ENUM | `dots`（サークル） | dots / line | FR-02 |
| `ma_show` | BOOL | false（TBD-06） | true/false | FR-04 |
| `ma_type` | ENUM | `ema`（TBD-04） | sma/ema/smma/lwma | `moving_averages` と同期（catalog.js:228） |
| `ma_length` | INT | 9（TBD-04） | ≥ 2 | `moving_averages` length（catalog.js:229） |
| `color` | COLOR | `rgba(123,104,238,1)` | — | tgp_btlm 既定色（catalog.js:96・lwc_chart.py:33） |

- `q_low=0.05`/`q_high=0.95` は固定（要件で `btlm_q5`/`btlm_q95` を明示）。パラメータ非公開。
- fitter は `ols` 固定（パラメータ非公開）。
- `display_mode`・`ma_show`・`ma_length` 等は要件に選択肢の実体が無いため新規定義（TBD で確定）。

### 6.2 画面構成・遷移

- 既存指標プロパティダイアログを流用（新規画面なし）。セクション構成は
  `moving_averages`（基本／平滑化／計算）と対称に「基本（source/maxbars/表示形式）」
  「平滑化（ma_show/ma_type/ma_length）」を提示する（catalog.js:226-246 に対称）。

### 6.3 外部システム連携仕様

| 連携先 | 連携方式 | データ形式 | 頻度 | エラー時動作 |
|---|---|---|---|---|
| `tgp_btlm`（`OlsBtlmFitter`/`build_btlm_bands`） | Python import 参照（無改変） | numpy 配列 / DataFrame | compute ごと | 例外伝播（KeyError/ValueError） |
| `moving_averages.core` | Python import 参照（無改変） | numpy 配列 | compute ごと | 例外伝播 |
| `common.applied_price` | Python import 参照（無改変） | numpy 配列 | compute ごと | `ValueError`（未知種別） |

### 6.4 通信プロトコル・データ形式

- 既存 compute アダプタ経路の JSON（系列 `{name, kind, data:[{time,value}]}`）に準拠
  （latest テスト `test_tgp_btlm_latest.py:106-113` の系列形状に一致）。プロトコル選定は
  既存基盤踏襲（新規選定なし）。

## 7. 非機能設計

### 7.1 性能設計・スケーラビリティ対策

| 要件 | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| 走査再計算コスト | TBD（実測で確定・§9.3 TBD-08） | OLS は 2×2 正規方程式の解析解（reference.py:52-63）＝ 1 窓 O(window)。全走査 O(N·maxbars)。増分計算（`latest_meta`）による末尾 K 点のみ再計算で常時更新コストを抑制（test_tgp_btlm_latest.py:77-83 の recurrence/K=1 既定に対称化） | full/latest 双方の compute 実測（既存 latest 検証枠組みを流用） |
| 応答（初回 full） | TBD（仮説：N=1e4・maxbars=100 で 2 秒未満／要実測） | ベクトル化（窓行列の一括生成）検討 | ベンチ実測 |

- 数値目標はユーザー未提示のため確定不能。仮説値は実測前の暫定であり、確定は TBD-08。
  （プロジェクト規約「数値化必須」に対し、実証なき断定を避け TBD 化する。）

### 7.2 可用性設計・障害対策

| 要件 | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| 計算の決定性 | 100%（同一入力→同一出力） | `OlsBtlmFitter` は決定論（R 非依存・reference.py:8-18） | 単体テスト（full=latest 末尾一致・test_tgp_btlm_latest.py:94-113 に対称） |
| 欠損耐性 | 窓不足バーは NaN で描画除外 | warm-up NaN（bands.py:81-83・lwc dropna 慣行 SPEC.md:63） | 単体テスト |

- 本指標はステートレス純粋計算のためサーバ可用性は既存基盤に従属（本指標固有の SLO なし）。

### 7.3 セキュリティ設計

- 認証・認可：既存指標基盤に従属（本指標は新規認証境界を持たない）。
- 入力検証：パラメータ型・範囲を既存 constraint 機構（`constraint_eval.js`）で検証。
  `maxbars≥1`・`ma_length≥2`・source enum 制約（catalog.js の制約定義に対称）。
- 通信暗号化・監査ログ：既存基盤に従属（本指標固有の追加なし）。
- 詳細脅威分析は security スキル成果物へ委譲（本指標は外部入力を伴わない純粋計算のため
  追加脅威面は限定的）。

### 7.4 運用・保守性設計

- ログ：既存 compute アダプタのログ機構に従属。
- 保守性：`tgp_btlm`/`moving_averages`/`applied_price` を参照するのみで重複実装を持たない
  （DRY・単一情報源）。MA 種別・ソース選択肢の変更は参照元 1 箇所の変更に追従する
  （catalog_schema.py の single source 思想・catalog_schema.py:1-17 に整合）。
- 拡張点：`display_mode`・MA 設定はパラメータ外部化。fitter を将来 `tgp` へ拡張する余地は
  ポート境界（`BtlmFitter`・core.py:82-99）で確保済み（本要件では ols 固定）。

## 8. 開発・運用方針

### 8.1 開発方法論・プロセス

- 参照実装優先・実証主義（プロジェクト規約 CLAUDE.md）。既存 4 指標のモジュール構造
  （core/bands or lwc_chart/tests）に対称化する。

### 8.2 品質保証方針

- 既存無改変の保証：`tgp_btlm`/`moving_averages`/`applied_price` に diff を出さない
  （新規ファイル追加のみ・registration 行を除く）。

### 8.3 テスト方針

- 単体：当日値抽出（窓末尾値＝`build_btlm_bands` 最終行との一致）、走査の非 repaint 性
  （過去バー値が後続データで不変）、ソース合成の `applied_price` 一致、MA 種別ごとの
  `moving_averages.core` 一致。
- 結合：catalog 登録・F3 系列名照合・latest 増分の末尾一致（test_tgp_btlm_latest.py に対称）。
- 受入：UI ダイアログでのパラメータ操作と描画（ドット/ライン切替・MA オン/オフ）。

### 8.4 リリース・デプロイメント方針

- 既存指標基盤のデプロイ経路に従う（本指標固有の追加なし）。

## 9. リスク・課題

### 9.1 技術的リスクと対策

| リスク | 影響度 | 発生確率 | 対策 | 出典／根拠 |
|---|---|---|---|---|
| 走査再計算が大 N で遅い | 中 | 中 | 解析解 OLS＋増分（latest K=1）＋ベクトル化 | test_tgp_btlm_latest.py:77-83（recurrence/K=1） |
| 「当日値」定義の誤り | 高 | 中 | 実装前に TBD-01/02 をユーザー確定 | bands.py に定義なし |
| 合成ソース注入の副作用 | 低 | 低 | 一時列は新指標 adapter 内に閉じる（tgp_btlm へ渡すのは列名のみ） | bands.py:67-73 |
| MA 適用対象の誤解 | 中 | 中 | TBD-03/04 を確定してから実装 | 要件非明示 |

### 9.2 スケジュール・リソースリスク

- 該当なし（設計のみ・実装未着手）。

### 9.3 今後の検討課題（TBD 一覧・ユーザー承認要）

| ID | 項目 | 確認が必要な理由 | 確認先／確認方法 |
|---|---|---|---|
| TBD-01 | 「当日結果」= 当てはめ窓末尾値（`mean[-1]`/`q_low[-1]`/`q_high[-1]`）で正しいか | `bands.py` は窓全体を返し当日値抽出コードが無い（bands.py:81-95）。定義が参照実装に不在 | ユーザー確認 |
| TBD-02 | 連結は「各バーで直近 maxbars 本を当てはめ直す走査（非 repaint）」で正しいか | 「繋いでいく」の意味（走査 vs 単一窓）が非一意。走査は現行 tgp_btlm に無い合成 | ユーザー確認 |
| TBD-03 | MA 平準化の適用対象は 3 系列すべてか、`btlm_mean` のみか | 要件「繋いだデータを平準化」が対象を特定しない | ユーザー確認 |
| TBD-04 | MA の期間（length）既定値・同期範囲（length/offset/timeframe まで同期するか） | 要件は「種別」「ソース」の同期のみ明示。期間・その他 MA パラメータの扱いが不明 | ユーザー確認 |
| TBD-05 | ソース既定値（`moving_averages` は `close`／`tgp_btlm` は `open`） | 同期先が異なる既定を持つ（catalog.js:230=close vs catalog.js:79=open） | ユーザー確認 |
| TBD-06 | MA 表示の既定（オン/オフ）と、ドットと MA の同時表示可否 | 要件「オプションで切替」の既定状態が非明示 | ユーザー確認 |
| TBD-07 | 指標の正式名称・`compute_id` | 新規指標の識別子が未定（ワーキング `btlm_track`） | ユーザー確認 |
| TBD-08 | 性能数値目標（応答時間・対応最大バー数） | ユーザーからの非機能数値目標が未提示。実測で確定要 | ユーザー確認＋ベンチ実測 |
| TBD-09 | ドット（サークル）の描画実体（lwc の point marker 機構）が v5 で満たせるか | 「ドット/サークル」の lwc v5 描画手段（lineType/pointMarkers 等）を実基盤で要確認 | 実 UI 検証（MEMORY: 実 UI で確認） |

## 10. 付録

### 10.1 用語集

| 用語 | 定義 |
|---|---|
| 当日値 | 当てはめ窓の最新位置（末尾）における OLS 予測値（本設計の定義・要 TBD-01 確定） |
| 走査（walk-forward） | 各バーで窓をずらし当てはめを反復する計算方式 |
| OLS 当てはめ | 単一区分ベイズ線形回帰（木分割・MCMC なし・reference.py:8-18） |
| ソース | 回帰対象の合成価格（旧 `price`・8 択） |
| F3 照合 | 系列名と catalog SeriesDef の突合検証（catalog.js の系列名照合） |

### 10.2 設計判断の根拠・トレードオフ

| 判断項目 | 採用 | 代替 | 根拠 | 出典区分 |
|---|---|---|---|---|
| アーキ | 参照拡張（新規モジュール） | tgp_btlm 拡張 | 既存無改変（OCP） | プロジェクト規約 |
| fitter | ols 固定 | tgp | 要件「ベースは ols」。R 依存回避（reference.py:8-11） | ユーザー要件 |
| 分位点 | 0.05/0.95 固定 | 可変 | 要件が `btlm_q5`/`btlm_q95` を明示 | ユーザー要件 |
| ソース合成 | `applied_price` 委譲 | 自前実装 | 合成価格の単一定義（applied_price.py:1-23） | 公式（DRY） |
| MA 計算 | `moving_averages.core` 参照 | 自前 MA | 種別同期の単一情報源 | プロジェクト規約 |
| 当日値抽出 | 窓末尾値 | 窓平均等 | 実測（bands.py 窓全体）から末尾が最新バー | （実務的推奨／仮説・要 TBD-01） |

### 10.3 参考資料（実測根拠・ファイル:行番号）

- `indigators/tgp_btlm/src/core.py:33`（DEFAULT_MAXBARS=100）、`:34-35`（q_low/q_high 0.05/0.95）、
  `:40,43-50`（`btlm_mean`/`btlm_q{pct}` 列名）、`:82-99`（`BtlmFitter` ポート）。
- `indigators/tgp_btlm/src/bands.py:34-95`（`build_btlm_bands`：price 既定 open・window=min(maxbars,n)・
  窓外 NaN・price 列名照合）。
- `indigators/tgp_btlm/src/reference.py:30-69`（`OlsBtlmFitter`：決定論的 R 非依存 OLS）。
- `indigators/tgp_btlm/SPEC.md:35-56`（計算定義・成果物列）。
- `indigators/moving_averages/README.md`（MA 4 種 API）、`src/core.py`（4 バッファ関数）、
  `src/lwc_chart.py:37-46`（`_SOURCE_TO_APPLIED` 8 択写像）、`:56-57,130-133`（warm-up）。
- `common/applied_price.py:32-45,109-150`（合成価格 8 種ディスパッチ）。
- `indigators/indicator_ui/web/js/usecase/catalog.js:69-113`（tgp_btlm def・price 4 択 catalog.js:79）、
  `:202-250`（moving_averages def・ma_type catalog.js:228・source 8 択 catalog.js:230・length catalog.js:229）。
- `indigators/indicator_ui/api/adapter/compute/catalog_schema.py:29-37,57-67`（既定値の単一情報源）。
- `indigators/indicator_ui/api/tests/latest/test_tgp_btlm_latest.py:77-113`（latest 増分・系列形状）。

### 10.4 関連する標準・規格

- SOLID（OCP：既存無改変の参照拡張）／DRY（合成価格・MA の単一情報源）。出典：一般設計原則。
- プロジェクト規約 `.doc/indicator-management-ui/INDICATOR_CALC_MODEL.md`（純粋関数・昇順・warm-up）。
