# btlm_trail 基本設計書

> 対象機能: チャート時間足のバー単位で `tgp_btlm` の OLS 回帰（`OlsBtlmFitter`）をローリング
> 当てはめ、各バーの窓末尾値（トレンド現在位置）と上下分位点を時系列連結してドット／ライン描画
> する新規オーバーレイ指標。名目 ols バンドと経験分位バンドの 2 方式・単一分位ペア・外れ値分位
> ライン・バンド内実績率（実現被覆率）／β／残差 σ の数値表示を持つ。`tgp_btlm` 本体のソース 8 択化を同梱変更として含む。
> ステータス: 基本設計（実装未着手）。
> 指標 ID: `btlm_trail`（ユーザー確定 2026-07-19）。
> 前身: `.doc/BTLM_DAILY_TRACK_BASIC_DESIGN.md`（`btlm_track` 時代の初版・本書で置換。旧書は設計記録として保存）。

---

## 1. 文書情報

- 作成日：2026-07-19
- バージョン：v1.0.0（基本設計・最終版）
- 作成者：system-basic-design エージェント
- 承認者：（未承認）
- 変更履歴：

  | 版 | 日付 | 変更内容 |
  |---|---|---|
  | v1.0.0 | 2026-07-19 | `btlm_track` 初版（`BTLM_DAILY_TRACK_BASIC_DESIGN.md`）を新名称 `btlm_trail` として全面改訂・新規作成。バンド方式 2 種・任意分位ペア・外れ値オフセットライン・実現被覆率・β／残差 σ 表示・MA 参考線・tgp_btlm 8 択化を確定仕様として反映 |
  | v1.0.1 | 2026-07-19 | **複数分位ペアをユーザー指示で撤回**。分位ペアは単一 `q_low`／`q_high` のみ（0<q_low<q_high<1 検証は温存）。UI の「追加ペア 2/3」4 項目と本番到達不能となった複数ペア配管（compute `quantile_pairs` 複数対応・adapter 追加ペア系列生成）をリポジトリ規約に従いテストごと撤去 |
  | v1.0.2 | 2026-07-19 | **UI 表示名を「被覆率」→「バンド内実績率」へ改名**（ユーザー確定）。設定ラベル・ツールチップ・読取欄系列名（payload `btlm_trail_coverage`→`btlm_trail_band_hit_rate`）を改名。**統計用語「実現被覆率（realized coverage）」は解説の併記語として保持**。計算・パラメータ名（`n_cov` 等）・挙動は byte 不変 |
  | v1.0.3 | 2026-07-19 | **「外れ値オフセット %」（`offset_pct`）を「外れ値分位」（`q_out`）へ置換**（ユーザー確定）。上側 `q_out`／下側 `1-q_out` に補助線を描画し、選択中のバンド方式（ols／経験分位）と同一規約で算出。有効条件 `q_high<q_out<1`・無効/空はオフ。`offset_pct` と %オフセット計算経路を到達不能化のため撤去（catalog/schema/golden/テスト同期）。帯本体・読取系列は byte 不変 |
  | v1.0.4 | 2026-07-19 | **ISSUE-141 是正**：経験分位の窓を §4.3 の「当該バー除外」（`d_{t-N}..d_{t-1}`）へ実装是正（従来 `[start:t+1]`＝当該バー込みの不整合）。経験分位バンド本体・外れ値分位の両方に適用（規約一致・自己参照遮断）。仕様（§4.3）は不変・実装のみ是正。経験分位帯の値が約 1 ランク変わる（想定内） |
  | v1.0.5 | 2026-07-19 | **MA 参考線をユーザー指示で削除**。設定「MA 参考線／種別／期間」（`ma_reference`/`ma_type`/`ma_length`）・系列 `btlm_trail_ma`・MA 配管（`ma_reference.py`・adapter 生成・`moving_averages.core` 動的ロード結線）をテストごと撤去（catalog/schema/golden 同期）。`moving_averages` 本体・`applied_price` は無改変。他機能（ドット/ライン・バンド 2 方式・q_out・β/バンド内実績率/σ・単一ペア・8 択ソース）は byte 不変 |
  | v1.0.6 | 2026-07-19 | **「系列表示（ドット/ライン）」をパラメーター（`display_mode`）から設定ダイアログ「スタイル」タブへ移設**（ユーザー確定・案A＝系列単位・既定ドット）。ゲート = SeriesDef 新フラグ `pointStyleEditable`（btlm_trail の mean/分位線のみ付与＝他指標のスタイルタブ挙動は不変）。永続化は既存 per-series style patch へ `display` 属性を追加（schema 変更不要）。`applySeriesStyle` に `display→pointMarkersVisible/lineVisible` 写像を追加（display 未指定系列は不変）。adapter は `display_mode` 撤去・常にドット既定 emit（計算 byte 不変）。catalog/schema/golden 同期 |
  | v1.0.7 | 2026-07-19 | **スタイルタブの「線種」と「ドット/ライン」を 1 つの 4 択（`dot`／`solid`／`dotted`／`dashed`・既定 `dot`）へ統合**（ユーザー確定）。対象系列（pointStyleEditable）の行は 色・線幅・統合 4 択 の 1 行構成（折返し解消）。`dot`＝サークル（`display=dots`）、線種＝ライン描画＋当該 `lineStyle`（`display=line`＋`style`）へ分解し既存 per-series patch へ整合保存（`{display, style}` スキーマ不変・移行不要・往復整合）。未付与系列（補助線・読取・全他指標）は従来 3 択（solid/dotted/dashed）で byte 不変。UI（properties_dialog）のみ変更・renderer/form_model/adapter/catalog は不変 |

- 確定仕様の正本：`/root/.claude/plans/kind-twirling-hollerith.md`（全項目を本書に反映）。
- 実証知見の正本：`.doc/BTLM_TRACK_ANALYSIS_FINDINGS.md`（結論 A〜E。非保証事項・数値は本書 §9.4／§10.1 に出典付き引用）。

## 2. プロジェクト概要

> 出力元：S-1 要件分析と設計方針決定

### 2.1 システム概要

- **位置付け**：既存指標基盤（`indigators/indicator_ui/`・`indigators/tgp_btlm/`・
  `indigators/moving_averages/`・`common/applied_price`）の上に追加する新規オーバーレイ指標。
  既存指標を改変せず参照のみで拡張する（SOLID・OCP／プロジェクト規約「主機能無改変の参照拡張」より）。
- **解決する課題**：現行 `tgp_btlm` は「直近 1 窓（`window = min(maxbars, n)` 本）の回帰当てはめ曲線」を
  描画する（実測 `indigators/tgp_btlm/src/bands.py:72-84`）。窓内位置 `1..window` の予測値を全て返すため、
  「各バー時点での当日推定（窓末尾値）」の時間推移は表現されない。本指標は各バーで回帰を当てはめ直し、
  その**窓末尾値のみ**を時系列連結して、トレンド現在位置・傾き（β）・帯の実現被覆率を可視化する。
- **一般化（本書の適用範囲）**：計算はチャート時間足のバー単位のローリング当てはめである
  （各バーで直近 `maxbars` 本に OLS を当てはめる）。「当日」とは最新バーを指す。日足チャートでは
  結果が日別に一致する（`.doc/BTLM_TRACK_ANALYSIS_FINDINGS.md` §2 の測定は JP225 日足 1,500〜3,676 本で実施）。

### 2.2 開発目的・背景

- **目的（ユーザー確定・plan §1「Context」）**：トレンドの現在位置（ドット）と傾き（β）の観測、
  当日バンドの実現被覆率の観測。
- **非目的（plan §1-1）**：翌時点方向の予測（実測 ≈50%＝情報なし・`FINDINGS §4` トレンド現在位置行）。
- **達成目標**：
  1. 動的窓 `maxbars`（既定 100）でローリング当てはめした OLS 窓末尾値 3 系列
     （`btlm_mean` および分位下端／上端）をドット（既定）／ラインで連結描画する。
  2. バンドを名目 ols バンド／経験分位バンドの 2 方式で描画可能にする（選択制）。
  3. 単一分位ペア `q_low`／`q_high`（既定 `[0.05, 0.95]`・任意値可）をバンド描画する（複数ペアは v1.0.1 で撤回）。
  4. 実現被覆率・β・残差 σ を数値表示する。
  5. （MA 参考線は v1.0.5 でユーザー指示により削除）
  6. `tgp_btlm` 本体のソースを 8 択へ拡張する（既存 4 択・既定 `open` 不変の追加拡張・§4.5）。

### 2.3 適用範囲・制約条件

#### 機能要件サマリー（要件 ID 一覧）

| 要件 ID | 要件 | 出所 |
|---|---|---|
| FR-01 | 各バーで直近 `maxbars` 本に OLS をローリング当てはめし、窓末尾値 3 系列（`btlm_mean`／分位下端／上端）を時系列連結する | plan §2（計算仕様） |
| FR-02 | 3 系列の描画形式をドット（サークル・既定）／ライン接続で切替可能にする。**切替は設定ダイアログ「スタイル」タブで系列単位に行う（v1.0.6・案A。`display_mode` param は撤去）** | plan §3／v1.0.6 |
| FR-03 | バンド方式を名目 ols バンド／経験分位バンドの 2 種から選択可能にする | plan §2（バンド方式） |
| FR-04 | 単一分位ペア `q_low`／`q_high`（既定 `[0.05, 0.95]`・`0<q_low<q_high<1` 検証・任意値可）をバンド描画する。**複数ペアは v1.0.1 でユーザー指示により撤回** | plan §2（分位パラメータ）／v1.0.1 改訂 |
| FR-05 | 分位ペアを帯として表示可能にする（ライン／塗りエリア選択・既定オフ） | plan §3（バンド表示） |
| FR-06 | 外れ値分位ライン（上側 `q_out`／下側 `1-q_out`・上下対称・既定オフ・`q_high<q_out<1` のみ有効）を描画可能にする。算出はバンド方式と同一規約 | plan §3／v1.0.3 改訂 |
| FR-07 | 実現被覆率を直近 N バー（既定 250・パラメータ）で分位ペアごとにローリング算出し数値表示する | plan §2（実現被覆率） |
| FR-08 | β（傾き＝回帰係数）を数値表示する（トレンド方向の正式判定値） | plan §3（数値表示）・FINDINGS §4 |
| FR-09 | 残差 σ を数値表示する（σ 正規化ストップ距離の計算資源） | plan §3（数値表示）・FINDINGS 結論 E |
| ~~FR-10~~ | ~~MA 参考線~~ **削除（v1.0.5・ユーザー指示）**。設定・系列・配管を撤去（`moving_averages` 本体は無改変） | plan §3／v1.0.5 |
| FR-11 | 計算ソースを `moving_averages` と同一 8 択（`applied_price` 参照・既定 `close`）にする | plan §2（ソース） |
| FR-12 | 窓幅 `maxbars` をパラメータとして持つ（既定 100・動的変更可） | plan §2 |
| FR-13 | 経験分位バンドの参照本数 N をパラメータとして持つ（既定 500） | plan §2（バンド方式）・要件（経験分位仕様） |
| FR-14 | `tgp_btlm` 本体のソースを 8 択へ拡張する（既存 4 択・既定 `open`・出力不変の追加拡張） | plan §4（同梱変更） |

#### 非機能要件サマリー（数値目標）

| 要件 ID | 非機能要件 | 数値目標 | 出所 |
|---|---|---|---|
| NFR-01 | 決定論性（同一入力→同一出力） | 100%（`OlsBtlmFitter` は R 非依存・乱数なし） | plan §2・reference.py:30-69 |
| NFR-02 | 非リペイント（確定バー値は後続データで不変） | 確定バー再計算差分 = 0 | plan §2・要件（決定論・非リペイント保証境界） |
| NFR-03 | 因果性（各バー値は当該バー以前のデータのみに依存） | 未来情報参照 0 | plan §2・要件（因果） |
| NFR-04 | 閉形式等価実装の数値一致（性能最適化時） | 参照実装との最大差 < 1e-6 | FINDINGS §6-5（実測済み） |
| NFR-05 | 走査再計算の応答時間 | TBD（ユーザー未提示・実測確定・§9.3 TBD-04） | plan（数値目標非提示） |

- 性能の絶対数値目標はユーザー未提示のため確定不能。§9.3 TBD-04 に委ね、実証なき断定を避ける
  （プロジェクト規約「実証なき憶測禁止」・skill §4「数値化必須」との両立として TBD 化）。

#### 制約条件

| 区分 | 制約 | 出典 |
|---|---|---|
| 技術（不変） | `build_btlm_bands`・`OlsBtlmFitter`・`moving_averages`・`common.applied_price` を**改変しない**（参照のみ） | プロジェクト規約（OCP・plan §変更対象「無改変」） |
| 技術（不変） | 新規依存を追加しない（numpy／pandas／stdlib のみ）。既存指標基盤の技術構成に従う | プロジェクト規約 |
| 技術（不変） | fitter は `ols`（`OlsBtlmFitter`）固定。`tgp`（R/tgp/rpy2）は対象外 | plan §2「fitter は ols 固定」 |
| 技術（同梱変更） | `tgp_btlm` 本体のソース 8 択化は既存 4 択・既定 `open`・出力を不変とする追加拡張のみ（非破壊） | plan §4（承認済み） |
| 運用 | 描画は既存 lightweight-charts v5 基盤（オーバーレイ pane） | 既存基盤 |
| 運用 | 検証は実 UI（ライブ／リプレイ）で表示・同期・非リペイントを実測確認する | MEMORY「実 UI で確認」・plan §Verification |

#### 設計上の課題と技術的リスク

| # | 課題／リスク | 内容 |
|---|---|---|
| C-1 | 走査再計算コスト | 各バーで OLS 当てはめを再計算すると O(N·maxbars)。N 大で性能懸念（§7.1） |
| C-2 | 閉形式等価実装の数値乖離 | 性能最適化で閉形式実装を用いる場合、参照実装との一致が崩れると非リペイント／被覆率が誤る（対策：回帰ガード必須・§7.1／NFR-04） |
| C-3 | 合成ソースの注入 | `bands.py:67-69` は実在列名のみ受理。合成ソース（hl2 等）は一時列注入が必要（§4.4） |
| C-4 | 経験分位バンドの因果性境界 | 参照分布に当該バーを含めると未来情報混入（リペイント）。当該バー除外の境界定義が必須（§4.3） |
| C-5 | 実現被覆率の誤用リスク | 無条件被覆率をタッチ時（条件付き）へ流用する誤読（FINDINGS 結論 D）。非保証事項へ明記（§9.4） |

## 3. システムアーキテクチャ

> 出力元：S-1 採用パターン選定 ／ S-2 アーキテクチャ設計

### 3.1 全体構成図

```
[既] OHLC DataFrame（昇順・open/high/low/close/time）
        │
        ▼
★[adapter] SourceResolver              ← source を common.applied_price で 1 次元価格列へ合成
        │   （close/open/high/low/hl2/hlc3/ohlc4/hlcc4・既定 close）
        ▼
★[usecase] TrailComputer               ← 各バー t で直近 maxbars 本に OLS をローリング当てはめ、
        │   （tgp_btlm.reference.OlsBtlmFitter を参照）  窓末尾の mean[-1]／q_low[-1]／q_high[-1]
        │                                 と β・残差 σ を収集
        ▼
   窓末尾値 3 系列（btlm_mean／分位下端／上端・時系列）＋ β 系列 ＋ σ 系列
        │
        ├─▶ ★[usecase] BandBuilder     ← バンド方式 2 種を分位ペアごとに構築
        │      ├ 名目 ols バンド : build_btlm_bands(q_low,q_high) の窓末尾値（無改変）
        │      └ 経験分位バンド   : 直近 N 本の乖離率経験分位（因果・当該バー除外）
        │
        ├─▶ ★[usecase] CoverageMeter   ← 分位ペアごとに直近 N バーの実現被覆率をローリング算出
        │
        ├─▶ ★[usecase] OutlierLiner    ← 外れ値分位 q_out（上側 q_out／下側 1-q_out・上下対称・既定オフ・
        │                                 バンド方式と同一規約）
        │
        │   （MA 参考線 TrailMaSmoother は v1.0.5 でユーザー指示により削除）
        ▼
★[adapter] 描画                        ← lwc へ系列追加（ドット/ライン・帯・外れ値分位線）
                                          ＋ β・バンド内実績率・残差 σ を数値表示
```

★ = 新規追加要素。既存要素（`build_btlm_bands`・`OlsBtlmFitter`・`moving_averages`・`applied_price`）は無改変で参照する。

### 3.2 アーキテクチャパターン選択理由

- **採用**：レイヤード（core / usecase / adapter）＋ ポート境界による参照拡張。
  既存 `indigators/*/src/`（core＝純粋ロジック、bands/lwc_chart＝成果物・描画アダプタ）の
  層構造に対称化する（プロジェクト規約「主機能と対称に」より）。
- **比較した代替案**：

  | 案 | 概要 | 評価軸（要件適合／既存無改変／運用コスト） | 採用可否 | 理由 |
  |---|---|---|---|---|
  | A. 参照拡張（新規モジュール・既存を import） | 新指標を独立モジュールとし `tgp_btlm`／`moving_averages`／`applied_price` を import 参照 | 要件適合◎／無改変◎／運用○ | **採用** | 既存無改変（OCP）を満たす。既存指標と同じ登録方式で UI 統合可能。バンド 2 方式・分位ペア・被覆率を新モジュール内に閉じられる |
  | B. `tgp_btlm` 拡張（既存 `bands.py` に走査＋経験分位モードを追加） | `build_btlm_bands` に走査・経験分位・被覆率を内蔵 | 要件適合○／無改変✕／運用△ | 棄却 | 共有モジュール `tgp_btlm` を改変（プロジェクト規約禁止事項・共有リソース破壊リスク） |
  | C. front（JS）側のみで連結・分位・被覆率を合成 | 既存 `tgp_btlm` 出力を front で再合成 | 要件適合✕／無改変◎／運用△ | 棄却 | `tgp_btlm` は 1 窓のみ計算し走査履歴・経験分位分布を持たない。front では走査再計算・因果境界を担保する入力が得られない |

- **出典区分**：（実務的推奨／仮説）。ただし「既存無改変」制約は プロジェクト規約（絶対遵守）。

### 3.3 技術スタック詳細

| 層 | 採用技術 | バージョン | 代替候補 | 採用根拠 |
|---|---|---|---|---|
| 計算（core/usecase） | Python + numpy | 既存構成に一致（無断変更禁止） | pandas 全面 | `OlsBtlmFitter` が numpy 実装（reference.py:25,46-63）。既存 core 層は numpy のみ（プロジェクト規約） |
| 成果物整形 | pandas | 既存構成に一致 | numpy のみ | `build_btlm_bands` が pandas.DataFrame を返す（bands.py:88-95）。既存アダプタと整合 |
| ソース合成 | `common.applied_price` | 既存共有層 | 自前実装 | 合成価格の単一定義（applied_price.py:32-44,109-150）。`moving_averages` も委譲（lwc_chart.py:37-45） |
| MA 計算 | `moving_averages.core` | 既存共有層 | 自前 MA | MA 種別同期の単一情報源（core.py 4 関数・catalog.js:228） |
| 描画 | lightweight-charts v5（既存同梱） | 既存基盤 | 追加ライブラリ | 新規依存禁止（制約） |

### 3.4 レイヤー構成・責務分担

| レイヤー | 責務 | 依存先 | 出典／根拠 |
|---|---|---|---|
| core（純粋ロジック） | 窓末尾値抽出規則・走査窓定義・経験分位（乖離率の経験 q）算出・被覆率算出（外部 I/O 非依存） | numpy のみ | 既存 core 層規約（tgp_btlm/core.py:1-23） |
| usecase | 走査再計算オーケストレーション（OLS 当てはめ反復・β／σ 収集・バンド 2 方式構築・被覆率・MA 平滑呼出） | `tgp_btlm.reference`／`tgp_btlm.bands`／`moving_averages.core` を参照 | 参照拡張（OCP） |
| adapter（成果物・描画） | ソース合成・系列 DataFrame 整形・lwc への系列追加（ドット／ライン／帯／オフセット線／MA 線）・数値表示配置 | usecase ＋ `common.applied_price` ＋ pandas | 既存 lwc_chart アダプタと対称（moving_averages/src/lwc_chart.py） |
| front / catalog | パラメータ定義・UI メタ・系列名照合（F3） | 既存 `usecase/catalog.js` レジストリ | 既存指標と同一登録方式（catalog.js:69-250） |

- 依存方向は core ← usecase ← adapter ← front の一方向（循環なし）。ドメイン層が UI に依存しない。

## 4. 機能設計

> 出力元：S-3 機能設計

### 4.1 機能一覧・優先度

| 機能 ID | 機能名 | 概要 | 優先度 | 対応要件 ID |
|---|---|---|---|---|
| F-01 | 窓末尾値ローリング走査計算 | 各バー t で直近 `maxbars` 本に OLS 当てはめ→窓末尾値・β・残差 σ 収集 | 高 | FR-01, FR-08, FR-09, FR-12 |
| F-02 | 3 系列連結描画 | `btlm_mean`／分位下端／上端を時系列連結 | 高 | FR-01 |
| F-03 | 描画形式切替 | ドット（サークル・既定）／ライン | 高 | FR-02 |
| F-04 | ソース合成 | 「ソース」選択（8 択・既定 close）→ OHLC 合成価格列を回帰対象に | 高 | FR-11 |
| F-05 | バンド方式選択 | 名目 ols バンド／経験分位バンド | 高 | FR-03 |
| F-06 | 単一分位ペア | `q_low`／`q_high`（既定 [0.05,0.95]・検証・任意値可）。複数ペアは v1.0.1 撤回 | 高 | FR-04 |
| F-07 | 帯表示 | 分位ペアを帯（ライン／塗りエリア・既定オフ）で表示 | 中 | FR-05 |
| F-08 | 外れ値分位ライン | 上側 `q_out`／下側 `1-q_out`（上下対称・既定オフ・バンド方式と同一規約） | 中 | FR-06 |
| F-09 | 実現被覆率算出 | 分位ペアごと直近 N バー（既定 250）ローリング被覆率を数値表示 | 高 | FR-07 |
| F-10 | β／残差 σ 表示 | 傾き β・残差 σ を数値表示 | 中 | FR-08, FR-09 |
| ~~F-11~~ | ~~MA 参考線~~ | **削除（v1.0.5・ユーザー指示）** | — | ~~FR-10~~ |
| F-12 | tgp_btlm ソース 8 択化（同梱変更） | 既存 4 択・既定 open・出力不変の追加拡張 | 中 | FR-14 |

### 4.2 機能詳細仕様（主要機能）

#### F-01 窓末尾値ローリング走査計算

- **入力**：昇順 OHLC DataFrame、`maxbars`（既定 100・`tgp_btlm/core.py:33 DEFAULT_MAXBARS=100`）、
  合成済みソース価格列、分位ペア集合（既定 `{(0.05, 0.95)}`・`tgp_btlm/core.py:34-35`）。
- **処理**：
  1. 合成ソース価格系列 `s[0..N-1]`（昇順）を得る。
  2. 各バー `t`（`t = t0 .. N-1`。`t0` は最小観測 3 点を満たす開始点・reference.py:49-50）について、
     直近窓 `s[t-window+1 .. t]`（`window = min(maxbars, t+1)`・bands.py:72）に OLS を当てはめる。
  3. 当てはめ結果のうち**窓末尾（最新位置）の値** `mean[-1]`／`q_low[-1]`／`q_high[-1]` を、
     バー `t` の当日値として収集する（`FINDINGS §1「ドット（窓末尾値）」`・`bands.py:84-86` の窓末尾位置）。
  4. 同一当てはめから β（回帰係数 `beta[1]`・reference.py:54）と残差 σ（`sqrt(s2)`・reference.py:59）を収集する。
  5. `t` を進めて時系列を得る。窓不足（観測 < 3）の先頭区間は NaN（bands.py:81-83・reference.py:49-50）。
- **参照実装との関係**：`build_btlm_bands`（bands.py:34-95）は 1 窓に対し窓全体の値を返す。本機能は
  「窓末尾値のみ」を各 `t` で収集する走査計算であり、`bands.py` を再利用する場合は各 `t` で
  `build_btlm_bands(df.iloc[:t+1], OlsBtlmFitter(), price=<列名>, maxbars=..., q_low=..., q_high=...)` を呼び、
  返り値の最終行を取る（既存無改変）。
- **性能最適化時の制約（NFR-04）**：閉形式の等価実装（窓を増分更新する 2×2 正規方程式の解析解）を
  性能最適化として用いてよい。ただし**参照実装 `build_btlm_bands` との数値一致テストを回帰ガードとして必須**とする
  （実測で最大差 < 1e-6 を確認済み・`FINDINGS §6-5`）。回帰ガードが崩れた実装は採用しない。
- **後条件**：3 系列＋β＋σ は入力 index を引き継ぎ、各バーに 0/1 点。各バー `t` の値は `t` 以前のデータのみに依存
  （因果・NFR-03）。確定バーは後続データ追加で不変（非リペイント・NFR-02）。
- **例外**：ソース列欠落 → `KeyError`（bands.py:67-69 準拠）。`q_low<q_high` 違反 → `ValueError`
  （bands.py:61-62 準拠）。観測 3 点未満の窓 → 当該バー NaN（reference.py:49-50）。空系列 → `ValueError`（bands.py:64-65）。

#### F-04 ソース合成（8 択・既定 close）

- **選択肢（`moving_averages` と同期）**：`close`／`open`／`high`／`low`／`hl2`／`hlc3`／`ohlc4`／`hlcc4`
  （実測 `catalog.js:230`・`MA_SOURCE_LABELS` catalog.js:207-211）。既定は `close`（`moving_averages` と同一・catalog.js:230）。
- **合成**：`common.applied_price.applied_price(kind, o, h, l, c)`（applied_price.py:109-150）へ委譲。
  写像は `moving_averages` の `_SOURCE_TO_APPLIED`（lwc_chart.py:37-45）と同一：
  `hl2→MEDIAN`／`hlc3→TYPICAL`／`hlcc4→WEIGHTED`／`ohlc4→OHLC4`。
- **注入（C-3 対応）**：`bands.py:67-69` は実在列名のみ受理するため、合成価格（hl2 等）は本指標 adapter が
  一時列として DataFrame に付与し、その列名を `price` 引数へ渡す（`tgp_btlm` 無改変・参照拡張）。
- **合成ソースの安全性（実証）**：合成ソース（hl2 等）が帯を歪める懸念は撤回済み（hl2/close 半幅比 0.979・
  `FINDINGS §5`）。

#### F-05／F-06 バンド方式選択・任意分位ペア

- **方式 1：名目 ols バンド**（正規仮定）。各バーの窓末尾値を `build_btlm_bands` の `q_low/q_high` 引数
  （既存無改変・bands.py:38-42）で得る。帯半幅は `norm_ppf(q) × 予測 SD`（reference.py:67-68・
  `norm_ppf` core.py:124-174）。実測の帯半幅は「1.645 ×（窓内残差標準偏差）」に一致し、終端補正は +1〜2% で無視可
  （`FINDINGS 結論 A`・`§5`）。
- **方式 2：経験分位バンド**（因果・非リペイント）。§4.3 に詳述。
- **単一分位ペア（FR-04・改訂 2026-07-19）**：分位ペアは `q_low`／`q_high` の**単一ペアのみ**とする。既定
  `[0.05, 0.95]`。`0 < q_low < q_high < 1` を検証する（bands.py:61-62 の制約と同一定義）。任意の分位値は
  設定可能（例 `0.10/0.90`）。ols 方式は `norm_ppf(q)`、経験分位方式は経験 q をそのまま適用する（参照実装
  `build_btlm_bands` の `q_low/q_high` 引数をそのまま利用＝無改変）。
  - **複数ペア（当初仕様）はユーザー指示で撤回（2026-07-19）**：UI の「追加ペア 2/3」項目と、本番到達不能と
    なった複数ペア配管（compute の `quantile_pairs` 複数対応・adapter の追加ペア系列生成）をリポジトリ規約
    （本番到達不能コードはテストごと撤去）に従い撤去した。単一ペアの機能・検証は完全温存。
- **系列名**：`btlm_mean`（core.py:40）、`btlm_q{int(round(q*100))}`（core.py:48-50）。分位 0.05/0.95 は
  `btlm_q5`/`btlm_q95`、0.25 は `btlm_q25`。系列名は F3 照合対象。

#### F-07 帯表示

- 分位ペアを帯として表示可能（ライン／塗りエリア選択・既定オフ・plan §3）。OFF 時は帯系列を出力しない。
- 用途は乖離スケールの記述量としての可視化に限る（「帯＝安全なエントリー水準」の解釈は非保証・§9.4）。

#### F-08 外れ値分位ライン（v1.0.3 改訂・旧「外れ値オフセット %」を置換）

- 入力は単一の**外れ値分位** `q_out`（例 0.99）。上側は分位 `q_out`、下側は分位 `1-q_out` の位置に
  補助線を上下対称で描画する（系列名 `btlm_trail_off_hi`／`btlm_trail_off_lo` は温存）。
- **算出は選択中のバンド方式と同一規約**：
  - 名目 ols → `mean ± norm_ppf(q_out)·pred_sd`（`norm_ppf(1-q_out) = -norm_ppf(q_out)` により上下対称）。
  - 経験分位 → 直近 `empirical_n` 本の乖離率の経験分位（因果・非リペイント。経験分位バンドと同一機構
    ＝**当該バー除外**の窓 `d_{t-N}..d_{t-1}` を参照。§4.3・C-4）。
- **有効条件 `q_high < q_out < 1`**。未入力（空）・`q_out ≤ q_high`・範囲外は黙って無効化＝補助線なし（既定オフ）。
- 用途はストップ位置の目安（バンドより外側の極端分位＝片側の極値水準）。「刈られない距離」であり
  「儲かる距離」ではない（帯＝安全水準ではない・§9.4）。旧仕様の固定 %（2.77% 等）表記は廃止。

#### F-09 バンド内実績率（実現被覆率）算出

> **用語対応（v1.0.2 改名）**：本指標の UI 表示名は「**バンド内実績率**」。統計用語の「実現被覆率
> （realized coverage）」に対応する（読取欄系列名 `btlm_trail_band_hit_rate`）。以下、UI 文脈では
> 「バンド内実績率」、統計的性質の記述では「実現被覆率」を用いる（同一量）。

- **定義**：分位ペア `(q_low, q_high)` が各確定バーで作る帯 `[L_t, U_t]` に対し、直近 N 確定バー
  （既定 250・パラメータ・plan §2）のうち「確定バーの `close` が当日帯 `[L_t, U_t]` 内」であった割合を
  ローリング算出する（plan §2「確定日 close が当日バンド内」）＝バンド内実績率。
- **バンドは校正しない**（plan §2）。バンド内実績率（実現被覆率）は表示値として観測する用途に限る。
- **数値根拠（表示の期待レンジ）**：名目 90% は達成されず、名目 ols バンドの実現被覆率は maxbars に単調依存する
  （20→86.8%／50→80.6%／100→78.7%／200→74.3%／400→69.4%・`FINDINGS 結論 B`）。経験分位バンドは
  ウォークフォワードで名目へ回復する（日足 N=250→87.1%／N=500→88.6%、1h N=250→86.6%・`FINDINGS 結論 B`）。

#### F-11 MA 参考線 — **2026-07-19 ユーザー指示で削除（v1.0.5）**

- **削除済**：設定項目「MA 参考線／種別／期間」（`ma_reference`/`ma_type`/`ma_length`）・系列 `btlm_trail_ma`・
  MA 平滑配管（`btlm_trail/src/ma_reference.py`＝`moving_averages.core` 動的ロード結線）を到達不能化のため
  テストごと撤去した（リポジトリ規約：本番到達不能コードはテストごと撤去）。
- **無改変**：`moving_averages` 本体・共有 `applied_price` は不変（参照実装）。ソース 8 択同期（`applied_price` 経由）は
  MA と無関係のため不変。
- 位置付け（削除前の実証）：方向確認用の参考線（β の劣化版）で、二重平滑は日次連動をほぼ消す（相関 0.02・
  `FINDINGS §4`）ものだった。以降は方向判定は β を用いる（§4 の役割分担）。

### 4.3 経験分位バンドの詳細仕様（因果・非リペイント）

- **乖離率の定義**：各確定バーの乖離率 `d_t = (close_t − btlm_mean_t) / btlm_mean_t`（ドット乖離＝トレンド成分を
  除去した残差・`FINDINGS §4「ドット乖離」`・結論 A）。
- **経験分位の算出（因果境界）**：バー `t` の帯は、**当該バー `t` を除く**直近 N 本 `d_{t-N} .. d_{t-1}` の
  経験分位 `q_low`／`q_high` を用いる（未来情報なし・要件「因果」）。帯は `L_t = btlm_mean_t × (1 + Q_low)`、
  `U_t = btlm_mean_t × (1 + Q_high)`（`Q` は経験分位値）。
- **更新粒度**：バーをまたいで日次（バー次）に更新し、当該バー内は固定（要件）。確定バーの帯は後続データ追加で
  不変（非リペイント・NFR-02）。
- **参照本数**：N は既定 500・パラメータ（FR-13）。N を長くするほど名目 90% に接近（残る 1〜3pp の不足はボラ局面
  変化に分位が遅れるため・`FINDINGS 結論 B`）。実現被覆率窓（F-09・既定 250）とは別パラメータである。
- **時間足頑健性**：改善幅（名目 ols 比 +約 10pp）は日足・1h で同等（`FINDINGS 結論 B` の日足／1h 表）。

### 4.4 処理フロー図

```
UI パラメータ（source/maxbars/display_mode/band_method/quantile_pairs/
              band_show/offset_pct/coverage_n/emp_n/ma_show/ma_type/ma_source/ma_length …）
      │
      ▼
compute 要求 → adapter: source 合成（applied_price・8 択・既定 close）
      │
      ▼
usecase: for t in [t0..N-1]:  OLS 当てはめ（直近 maxbars）→ 窓末尾値 3 点＋β＋σ 収集
      │
      ├─▶ band_method=nominal :  build_btlm_bands(q_low,q_high) 窓末尾（分位ペアごと）
      ├─▶ band_method=empirical: 直近 emp_n 本の乖離率経験分位（当該バー除外・因果）
      │
      ├─▶ CoverageMeter: 分位ペアごと直近 coverage_n バーの close∈帯 割合
      ├─▶ OffsetLiner:   band_show かつ offset_pct>0 → 上下対称補助線
      │
      ▼（ma_show=true）
usecase: MA 平滑（btlm_mean のみ・ma_type/ma_source/ma_length）→ MA ライン系列
      │
      ▼
adapter: lwc へ系列追加（display_mode）→ ドット/ライン・帯・オフセット線・MA 線
         ＋ β・実現被覆率（ペアごと）・残差 σ を数値表示
```

### 4.5 変更仕様：tgp_btlm 本体のソース 8 択化（同梱変更・FR-14）

- **現状**：`tgp_btlm` の `price` 選択肢は 4 種 `['open','high','low','close']`・既定 `open`（実測 catalog.js:79）。
  backend `build_btlm_bands` の `price` 引数は任意の実在列名を受理する（bands.py:38,67-69）。
- **変更内容**：`tgp_btlm` の catalog `price` 選択肢を 8 択 `['open','high','low','close','hl2','hlc3','ohlc4','hlcc4']` へ
  拡張する（`moving_averages` と同期）。合成ソース（hl2 等）は adapter が一時列を注入して `price` 引数へ列名を渡す
  （§4.4／F-04 と同一機構）。
- **不変条件（非破壊・plan §4「既存 4 択・既定 open・出力は不変」）**：
  - 既定値は `open` のまま変更しない（既存挙動の非退行）。
  - 既存 4 択（open/high/low/close）の計算結果・出力系列は byte 単位で不変とする。
  - 追加は選択肢拡張（additive）のみ。`build_btlm_bands`・`OlsBtlmFitter` は無改変。
- **変更範囲**：`tgp_btlm` catalog の `price` enum 定義と価格列解決のみ（plan §変更対象「承認済み」）。
- **検証（回帰ガード）**：既定 `open` および既存 4 択の出力が変更前と一致することをテストで確認する
  （plan §Verification「tgp_btlm 既定 byte 不変」）。

### 4.6 業務フロー・ユースケース

- UC：ユーザーが指標一覧から `btlm_trail` を選択 → パラメータダイアログで source／maxbars／表示形式／
  バンド方式／分位ペア／帯表示／オフセット％／被覆率窓／MA 設定を調整 → チャートにドット（又はライン）＋
  任意で帯・オフセット線・MA を重畳。β・実現被覆率・残差 σ を数値で読む。
- 既存指標管理 UI（`.doc/indicator-management-ui/基本設計書.md`）のダイアログ機構を流用する（新規 UI 機構は追加しない）。

## 5. データ設計

> 出力元：S-3 データ設計

### 5.1 データモデル概要

- 本指標は DB・永続状態を持たない純粋計算（入力 OHLC 配列 → 出力系列）。
  `.doc/indicator-management-ui/INDICATOR_CALC_MODEL.md`（入力配列限定型）に準拠。

### 5.2 主要エンティティ定義（概念）

| エンティティ | 概要 | 主要属性 | 関連 |
|---|---|---|---|
| ソース価格系列 | 合成後 1 次元価格列 | 種別（8 択・既定 close）、値配列（昇順） | OHLC 入力から生成 |
| 窓末尾値系列（連結） | バーごとの窓末尾 OLS 予測 | `btlm_mean`／`btlm_q{lo}`／`btlm_q{hi}`、time、value | ソース価格系列に依存 |
| 回帰統計系列 | バーごとの傾き・残差散らばり | β（回帰係数）、残差 σ、time | 窓末尾値系列と同一当てはめから生成 |
| バンド（分位ペアごと） | 名目 ols／経験分位いずれかの帯 | 方式、q_low、q_high、下端／上端 value | 窓末尾値系列（名目）／乖離率分布（経験） |
| 実現被覆率 | 分位ペアごとの直近 N バー被覆割合 | q_low、q_high、N（既定 250）、被覆率 | バンド＋確定バー close |
| 外れ値分位系列 | 上側 `q_out`／下側 `1-q_out` の上下対称補助線 | q_out、上端／下端 value | バンド方式と同一規約（有効時のみ） |
| MA 参考系列 | `btlm_mean` の MA | ma_type、ma_source、length、value | `btlm_mean` 系列に依存（表示 ON 時のみ） |

- 成果物列名は `tgp_btlm` の命名規則を継承：`btlm_mean`（core.py:40）、
  `btlm_q{int(round(q*100))}` → `btlm_q5`/`btlm_q95`（core.py:48-50）。系列名は F3 照合対象。

### 5.3 データフロー図

- §3.1 全体構成図に同じ（OHLC → 合成 → 走査 OLS → 窓末尾値／β／σ → バンド 2 方式 → 被覆率／オフセット／MA → 描画）。

### 5.4 データライフサイクル

- 揮発（計算のたびに再生成）。保持・アーカイブ・削除ポリシーは持たない（純粋計算）。
  確定バーの値は同一入力に対し不変（非リペイント・NFR-02）。

## 6. インターフェース設計

> 出力元：S-3 インターフェース設計

### 6.1 API 設計概要

- 既存 compute 経路（`indigators/indicator_ui/api/`・`GET /catalog` ＋ compute 呼出）へ新規 `compute_id`
  （`btlm_trail`）を登録して統合する（既存バインディングと同一機構）。新規エンドポイントは追加しない。

#### パラメータ一覧（設計案）

| パラメータ | 型 | 既定 | 選択肢／制約 | 出所・実測根拠 |
|---|---|---|---|---|
| `source` | ENUM | `close` | close/open/high/low/hl2/hlc3/ohlc4/hlcc4 | `moving_averages` と同期（catalog.js:230）・plan §2 |
| `maxbars` | INT | 100 | ≥ 1 | tgp_btlm（core.py:33・catalog.js:81） |
| `display_mode` | ENUM | `dots`（サークル） | dots / line | FR-02・plan §3 |
| `band_method` | ENUM | `nominal` | nominal（名目 ols）/ empirical（経験分位） | FR-03・plan §2 |
| `quantile_pairs` | LIST[(FLOAT,FLOAT)] | `[(0.05, 0.95)]` | 各ペア `0<q_low<q_high<1`・複数可 | FR-04・plan §2（bands.py:61-62） |
| `band_show` | ENUM | `off` | off / line / area | FR-05・plan §3 |
| `q_out` | FLOAT | null（オフ） | `q_high < q_out < 1`（無効/空はオフ・上下対称） | FR-06・v1.0.3 |
| `coverage_n` | INT | 250 | ≥ 1 | FR-07・plan §2 |
| `emp_n` | INT | 500 | ≥ 1 | FR-13・plan §2（経験分位参照本数） |
| `ma_show` | BOOL | false | true/false | FR-10・plan §3（既定オフ） |
| `ma_type` | ENUM | `ema` | sma/ema/smma/lwma | `moving_averages` と同期（catalog.js:228） |
| `ma_source` | ENUM | `close` | 8 択（同上） | `moving_averages` と同期（catalog.js:230） |
| `ma_length` | INT | 9（TBD-01） | ≥ 2 | `moving_averages` length（catalog.js:229）。同期範囲が期間まで及ぶか要確認 |
| `color` | COLOR | `rgba(123,104,238,1)` | — | tgp_btlm 既定色（catalog.js 系） |

- fitter は `ols` 固定（パラメータ非公開・plan §2）。
- 分位ペアの `q_low/q_high` は可変（FR-04）。既定ペアは `[0.05, 0.95]`。
- `ma_length` の既定・同期範囲は plan が「種別／ソース同期」のみ明示（期間は非明示）。§9.3 TBD-01。

### 6.2 画面構成・遷移

- 既存指標プロパティダイアログを流用（新規画面なし）。セクション構成は `moving_averages`（基本／平滑化／計算）と
  対称に「基本（source/maxbars/表示形式）」「バンド（band_method/quantile_pairs/band_show/offset_pct/coverage_n/emp_n）」
  「参考線（ma_show/ma_type/ma_source/ma_length）」を提示する（catalog.js:226-246 に対称）。
- 数値表示（β・実現被覆率・残差 σ）の配置は既存指標の数値表示機構に対称化する（§9.3 TBD-02）。

### 6.3 外部システム連携仕様

| 連携先 | 連携方式 | データ形式 | 頻度 | エラー時動作 |
|---|---|---|---|---|
| `tgp_btlm`（`OlsBtlmFitter`／`build_btlm_bands`） | Python import 参照（無改変） | numpy 配列 / DataFrame | compute ごと | 例外伝播（KeyError/ValueError・bands.py:61-69） |
| `moving_averages.core` | Python import 参照（無改変） | numpy 配列 | compute ごと（MA 表示 ON 時のみ） | 例外伝播 |
| `common.applied_price` | Python import 参照（無改変） | numpy 配列 | compute ごと | `ValueError`（未知種別・applied_price.py:150） |

### 6.4 通信プロトコル・データ形式

- 既存 compute アダプタ経路の JSON（系列 `{name, kind, data:[{time,value}]}`）に準拠。プロトコル選定は既存基盤踏襲
  （新規選定なし）。数値表示（β・被覆率・σ）は系列外のメタ値として既存の数値表示経路に載せる（§9.3 TBD-02）。

## 7. 非機能設計

> 出力元：S-4 品質特性の担保

### 7.1 性能設計・スケーラビリティ対策

| 要件 ID | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| NFR-05 | TBD（実測で確定・§9.3 TBD-04） | OLS は 2×2 正規方程式の解析解（reference.py:52-63）＝ 1 窓 O(window)。全走査 O(N·maxbars)。末尾 K 点のみ再計算する増分更新（latest）で常時更新コストを抑制 | full/latest 双方の compute 実測（既存 latest 検証枠組みを流用） |
| NFR-04 | 参照実装との最大差 < 1e-6 | 閉形式等価実装（増分更新）を用いる場合、`build_btlm_bands` との数値一致テストを回帰ガードとして必須化（実測済み・FINDINGS §6-5） | 単体テスト（各バー窓末尾値＝`build_btlm_bands` 最終行との差 < 1e-6） |
| 経験分位算出 | TBD（§9.3 TBD-04） | 直近 emp_n 本の乖離率経験分位はローリング分位で算出。全走査 O(N·emp_n)（分位計算を含む） | ベンチ実測 |

- 性能の絶対数値目標はユーザー未提示のため確定不能。仮説値の断定は避け §9.3 TBD-04 に委ねる
  （プロジェクト規約「実証なき憶測禁止」・skill §4「数値化必須」の両立として TBD 化）。

### 7.2 可用性設計・障害対策

| 要件 ID | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| NFR-01 | 決定論 100%（同一入力→同一出力） | `OlsBtlmFitter` は R 非依存・乱数なし（reference.py:30-69）。経験分位・被覆率も決定論的算術 | 単体テスト（再計算で同一出力） |
| NFR-02 | 非リペイント（確定バー再計算差分 = 0） | 各バー値は当該バー以前のデータのみで算出。経験分位は当該バー除外の直近 N 本を参照（§4.3） | 単体テスト（過去バー値が後続データ追加で不変） |
| NFR-03 | 因果性（未来情報参照 0） | 走査窓は `s[t-window+1..t]`。経験分位は `d_{t-N}..d_{t-1}`（当該バー除外） | 単体テスト（因果境界の検証） |
| 欠損耐性 | 窓不足バーは NaN で描画除外 | warm-up NaN（bands.py:81-83・観測 3 点未満 reference.py:49-50） | 単体テスト |

- 本指標はステートレス純粋計算のためサーバ可用性は既存基盤に従属（本指標固有の SLO なし）。

#### 決定論・非リペイントの保証境界（要件明示事項）

| 項目 | 保証する | 保証しない |
|---|---|---|
| 確定バー（過去バー） | 同一入力に対し値不変（非リペイント・NFR-02）。後続バー追加で再計算しても差分 0 | — |
| 最新バー（当日＝未確定バー） | 当該バー以前のデータのみに依存（因果） | 未確定バーはバー確定まで暫定値（新ティックで更新される。plan §2「当日は日確定まで暫定」） |
| 経験分位バンド | 当該バー除外の直近 N 本参照＝因果・非リペイント（§4.3） | 参照本数 N 未満の先頭区間は NaN |
| 実現被覆率 | 確定バーの close と当日帯で算出＝因果 | 直近 N 未満の先頭区間は算出不能 |

### 7.3 セキュリティ設計

- 認証・認可：既存指標基盤に従属（本指標は新規認証境界を持たない）。
- 入力検証：パラメータ型・範囲を既存 constraint 機構（`constraint_eval.js`）で検証。
  `maxbars≥1`・`ma_length≥2`・分位ペア `0<q_low<q_high<1`（bands.py:61-62 と同一定義）・source enum 制約。
- 通信暗号化・監査ログ：既存基盤に従属（本指標固有の追加なし）。
- 詳細脅威分析は security スキル成果物へ委譲（本指標は外部入力を伴わない純粋計算のため追加脅威面は限定的）。

### 7.4 運用・保守性設計

- ログ：既存 compute アダプタのログ機構に従属。
- 保守性：`build_btlm_bands`／`OlsBtlmFitter`／`moving_averages`／`applied_price` を参照するのみで重複実装を持たない
  （DRY・単一情報源）。MA 種別・ソース選択肢の変更は参照元 1 箇所の変更に追従する。
- 拡張点：`display_mode`・バンド方式・分位ペア・オフセット％・MA 設定はパラメータ外部化。fitter を将来 `tgp` へ
  拡張する余地はポート境界（`BtlmFitter`・core.py:82-99）で確保済み（本要件では ols 固定）。
- 拡張候補（保証外・記録のみ）：価格−ドット乖離の平均回帰性（`FINDINGS 結論 C`）・σ 正規化ストップ距離
  （`FINDINGS 結論 E`）は分析知見であり本指標の機能・保証には含めない（§9.4）。

## 8. 開発・運用方針

> 出力元：S-4 品質特性の担保

### 8.1 開発方法論・プロセス

- 参照実装優先・実証主義（プロジェクト規約 CLAUDE.md）。既存指標のモジュール構造（core/bands or lwc_chart/tests）に
  対称化する。実装は TDD・GitFlow feature ブランチ（plan §実行ステップ 3）。

### 8.2 品質保証方針

- 既存無改変の保証：`build_btlm_bands`／`OlsBtlmFitter`／`moving_averages`／`applied_price` に diff を出さない
  （新規ファイル追加のみ）。`tgp_btlm` 8 択化は catalog の enum 定義と価格列解決に限り、既定 `open`・既存 4 択出力の
  byte 不変を回帰ガードで担保する（§4.5）。

### 8.3 テスト方針

- 単体：
  - 窓末尾値抽出＝`build_btlm_bands` 最終行との一致（閉形式実装時は差 < 1e-6・NFR-04）。
  - 走査の非リペイント性（過去バー値が後続データで不変・NFR-02）。
  - 経験分位バンドの因果境界（当該バー除外・NaN 先頭区間・§4.3）。
  - 実現被覆率算出（分位ペアごと・直近 N・close∈帯 割合）。
  - ソース合成の `applied_price` 一致（8 択）。
  - MA 種別ごとの `moving_averages.core` 一致（`btlm_mean` のみ適用）。
  - tgp_btlm 8 択化：既定 `open`・既存 4 択の出力 byte 不変。
- 結合：catalog 登録・F3 系列名照合・latest 増分の末尾一致。
- 受入（実 UI・ユーザー厳命）：ライブ／リプレイ両モードでドット／ライン・帯・オフセット線・β・被覆率・σ 表示、
  MA オン／オフ、非リペイントをスクリーンショット実測確認。tgp_btlm／moving_averages の既定挙動非退行を確認。

### 8.4 リリース・デプロイメント方針

- 既存指標基盤のデプロイ経路に従う（本指標固有の追加なし）。

## 9. リスク・課題

> 出力元：S-1 リスク列挙 ／ S-5 設計検証

### 9.1 技術的リスクと対策

| リスク | 影響度 | 発生確率 | 対策 | 出典／根拠 |
|---|---|---|---|---|
| 走査再計算が大 N で遅い | 中 | 中 | 解析解 OLS＋増分（latest K）＋閉形式等価実装 | reference.py:52-63・§7.1 |
| 閉形式実装が参照実装と乖離 | 高 | 低 | 数値一致テストを回帰ガード必須（差 < 1e-6） | NFR-04・FINDINGS §6-5 |
| 経験分位の因果境界誤り（未来混入） | 高 | 中 | 当該バー除外の直近 N 本参照を単体テストで検証 | §4.3・NFR-03 |
| 実現被覆率の誤読（無条件→条件付き流用） | 中 | 中 | 非保証事項へ明記（帯＝安全水準ではない） | FINDINGS 結論 D・§9.4 |
| tgp_btlm 8 択化で既定挙動退行 | 高 | 低 | 既定 open・既存 4 択の byte 不変を回帰ガード | §4.5・plan §Verification |
| 合成ソース注入の副作用 | 低 | 低 | 一時列は本指標 adapter 内に閉じる（tgp_btlm へは列名のみ） | bands.py:67-73 |

### 9.2 スケジュール・リソースリスク

- 該当なし（設計のみ・実装未着手）。

### 9.3 今後の検討課題（TBD 一覧）

| ID | 項目 | 確認が必要な理由 | 確認先／確認方法 |
|---|---|---|---|
| TBD-01 | MA 参考線の期間（`ma_length`）既定値・同期範囲（length/offset/timeframe まで同期するか） | plan は MA の「種別／ソース同期」のみ明示。期間・その他 MA パラメータの扱いが非明示 | ユーザー確認 |
| TBD-02 | β・実現被覆率・残差 σ の数値表示の UI 配置・経路 | plan は「数値表示」を明示するが表示位置・既存数値表示機構の流用可否が未確定 | ユーザー確認＋実 UI 検証 |
| TBD-03 | 複数分位ペア同時描画時の色・凡例割り当て規則 | plan は「複数ペア→複数バンド同時描画」だが色／凡例割当規則が非明示 | 内部設計／実 UI 検証 |
| TBD-04 | 性能数値目標（応答時間・対応最大バー数） | ユーザーからの非機能数値目標が未提示。実測で確定要 | ユーザー確認＋ベンチ実測 |
| TBD-05 | ドット（サークル）の描画実体（lwc v5 の point marker 機構）が満たせるか | 「ドット／サークル」の lwc v5 描画手段を実基盤で要確認 | 実 UI 検証（MEMORY: 実 UI で確認） |

### 9.4 非保証事項（設計書明記・出典付き）

> 出典はすべて `.doc/BTLM_TRACK_ANALYSIS_FINDINGS.md`（実測値。憶測・理論値ではない）。

1. **ドットは記述量（トレンド現在位置）であり予測値ではない**。翌時点方向は ≈50%＝情報なし（FINDINGS §4「トレンドの現在位置」行・plan §5 筆頭）。
2. **名目 90% と実現被覆率は乖離する**。名目 ols バンドの実現被覆率は maxbars 単調減少（100 本で 78.7%・FINDINGS 結論 B）。実現被覆率は本指標の表示値（F-09）で観測する。
3. **バンド幅は市場ボラと線形モデル不適合の双方で拡大する**（実測相関 各 0.62・交絡未分離）。幅の拡大をリスク増大と断定できない（FINDINGS 結論 B）。
4. **ols は単一区分線形（木分割・MCMC なし）**。非線形・レジーム転換の表現力は元々ない（FINDINGS §6-4・reference.py:8-18）。
5. **ドット前日比の符号はトレンド方向と 63.9% しか一致しない**。方向判定は β を用いる（FINDINGS §4・plan §5）。
6. **「帯＝安全なエントリー水準」ではない**。無条件被覆率（88.6% 等）はタッチ時（条件付き）へ流用できない。下端初割れ後 +1h で 75% が帯外・最大逆行 >0.5% が無条件比 3.3 倍（FINDINGS 結論 D）。帯幅は乖離スケールの記述量としてのみ利用する。
7. **価格−ドット乖離の平均回帰性は本指標の機能・保証に含めない**。全 14 年・非重複で 5% 下方乖離→21 日先 +3.00%（超過 +1.63pp・プール時のみ p=0.036・単独では戦略化不十分・ロング側のみ）。分析知見として記録のみ（FINDINGS 結論 C・plan §5）。
8. **外れ値オフセット（ストップ距離）は「刈られない距離」であり「儲かる距離」ではない**。24h・90% 生存 ≒ 2.77%（1.14σ）は参考併記であり、標本 79 件で p90/p95 の推定は粗い（別期間で要再検証）。帯端ちょうどのストップは 75% が帯外継続でほぼ即死（FINDINGS 結論 E・plan §3）。

## 10. 付録

### 10.1 用語集

| 用語 | 定義 |
|---|---|
| 当日値（窓末尾値・ドット） | 各バーで直近 maxbars 本に当てはめた OLS の窓末尾（最新位置）予測値。本指標の原子。確定バーは不変（FINDINGS §1） |
| 走査（ローリング当てはめ） | 各バーで窓をずらし当てはめを反復する計算方式（チャート時間足のバー単位） |
| β（傾き） | 当てはめ直線の回帰係数（reference.py:54 `beta[1]`）。トレンド方向の正式判定値 |
| 残差 σ | 回帰直線まわりの価格の散らばり（`sqrt(s2)`・reference.py:59）。σ 正規化ストップ距離の計算資源 |
| 名目 ols バンド | 残差正規仮定で `norm_ppf(q)×予測 SD` を引いた帯（FINDINGS 結論 A） |
| 経験分位バンド | 直近 N 本の乖離率の経験分位で引いた帯（因果・非リペイント・§4.3） |
| 乖離率 | `(close − btlm_mean)/btlm_mean`（ドット乖離＝トレンド除去後の残差・FINDINGS §4） |
| 実現被覆率 | 直近 N 確定バーで close が当日帯内であった割合（分位ペアごと・F-09） |
| 外れ値オフセットライン | 帯端から任意 % 上下対称の補助線（ストップ距離可視化・既定オフ） |
| F3 照合 | 系列名と catalog SeriesDef の突合検証（catalog.js の系列名照合） |

### 10.2 設計判断の根拠・トレードオフ

| 判断項目 | 採用 | 代替 | 根拠 | 出典区分 |
|---|---|---|---|---|
| アーキ | 参照拡張（新規モジュール） | tgp_btlm 拡張／front 合成 | 既存無改変（OCP）・走査履歴と因果境界を新モジュールに閉じる | プロジェクト規約 |
| fitter | ols 固定 | tgp | plan「fitter は ols 固定」。R 依存回避（reference.py:8-11） | plan §2 |
| バンド方式 | 名目 ols／経験分位の 2 方式選択 | 名目のみ | 経験分位はウォークフォワードで名目へ回復（N=500 で 88.6%） | plan §2・FINDINGS 結論 B |
| 分位ペア | 任意ペア（既定 0.05/0.95） | 固定 | plan「任意に追加可能」。build_btlm_bands の q 引数を無改変利用 | plan §2 |
| 経験分位の因果境界 | 当該バー除外の直近 N 本 | 当該バー含む | 未来情報混入（リペイント）を排除 | 要件（因果・非リペイント） |
| 被覆率 | 校正せず表示のみ | 帯を校正 | plan「バンドは校正しない」。実現被覆率は観測用途 | plan §2 |
| ソース合成 | `applied_price` 委譲（8 択・既定 close） | 自前実装 | 合成価格の単一定義（applied_price.py:109-150）。moving_averages と同期 | 公式（DRY）・plan §2 |
| MA 参考線 | btlm_mean のみ・既定オフ | 3 系列適用 | 二重平滑で日次連動消失（相関 0.02）。方向確認用の参考線に格下げ | plan §3・FINDINGS §4 |
| 性能最適化 | 閉形式等価実装＋回帰ガード必須 | 参照実装直呼びのみ | 走査 O(N·maxbars) 抑制。ただし数値一致（<1e-6）を回帰ガードで担保 | FINDINGS §6-5 |
| tgp_btlm 8 択化 | 既定 open・既存 4 択出力不変の追加拡張 | 既定も変更 | 非破壊（既存挙動非退行） | plan §4（承認済み） |

### 10.3 参考資料（実測根拠・ファイル:行番号／現行コードで再検証済み 2026-07-19）

- `indigators/tgp_btlm/src/core.py:33`（`DEFAULT_MAXBARS=100`）、`:34-35`（`q_low/q_high` 0.05/0.95）、
  `:40,43-50`（`btlm_mean`／`btlm_q{pct}` 列名）、`:82-99`（`BtlmFitter` ポート）、`:102-121`（`make_design`）、`:124-174`（`norm_ppf`）。
- `indigators/tgp_btlm/src/bands.py:34-95`（`build_btlm_bands`：price 既定 open・`window=min(maxbars,n)`・
  窓外 NaN・price 列名照合・q 検証 `:61-62`・KeyError `:67-69`・窓末尾 fill `:81-86`）。
- `indigators/tgp_btlm/src/reference.py:30-69`（`OlsBtlmFitter`：決定論 R 非依存 OLS・β `:54`・残差 s2 `:57-59`・
  予測 SD `:61-63`・q 分位 `:67-68`・観測 3 点未満 ValueError `:49-50`）。
- `common/applied_price.py:32-44`（`AppliedPrice` 8 種）、`:109-150`（合成価格 8 種ディスパッチ）。
- `indigators/moving_averages/src/core.py`（MA 4 種バッファ関数）、`src/lwc_chart.py:37-45`（`_SOURCE_TO_APPLIED` 8 択写像）、`:169`（`add_moving_averages`）。
- `indigators/indicator_ui/web/js/usecase/catalog.js:79`（tgp_btlm price 4 択・既定 open）、`:81`（maxbars INT 100 min1）、
  `:82-88`（q_low/q_high）、`:206`（`MA_TYPE_LABELS`）、`:207-211`（`MA_SOURCE_LABELS`）、`:228`（ma_type 4 択）、
  `:229`（length INT 9 min2）、`:230`（moving_averages source 8 択・既定 close）。
- `.doc/BTLM_TRACK_ANALYSIS_FINDINGS.md`（結論 A〜E・§2 測定条件・§4 役割分担・§5 反証・§6 限界／§6-5 閉形式一致）。
- `/root/.claude/plans/kind-twirling-hollerith.md`（確定仕様の正本）。

### 10.4 関連する標準・規格

- SOLID（OCP：既存無改変の参照拡張）／DRY（合成価格・MA の単一情報源）。出典：一般設計原則。
- プロジェクト規約 `.doc/indicator-management-ui/INDICATOR_CALC_MODEL.md`（純粋関数・昇順・warm-up）。
</content>
</invoke>
