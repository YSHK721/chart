# PRO!fitHLBand 移植仕様書

## 1. Objective（目的）
高安レンジ `range = high - low` の系列全体の `平均` と `母標準偏差帯`（+1.65σ / +1.96σ /
+2.58σ）を算出し、(A) 別ウィンドウに `hl_range` のヒストグラムと σ 水準線 4 本を描き、
(B) メインチャートに、最新足の High から各帯を**減算**・最新足の Low へ各帯を**加算**して
投影した水平バンド 8 本を重ねる。レンジのボラティリティ水準と、最新足を基準とした到達目安
（H/L からの ±σ 距離）を可視化する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（range → 全系列平均・母σ帯 → 最新 H/L への 8 投影 → subwindow 範囲）/
  描画の**意味**（separate window のヒストグラム＋σ 水準線 4 本、main overlay の水平線 8 本）/
  入力（CSV → OHLC）。
- 対象外:
  - **MT4 描画オブジェクト（`ObjectCreate` / `ObjectDelete` の OBJ_TREND）そのもの**。
    本移植は 8 本の投影値（high_*/low_*）を算出し、移植先では水平線として再表現する
    （オブジェクトのライフサイクル管理・名前付き削除は移さない。描画の意味のみ移植）。
  - ブローカー接続・チャートデータ供給（`OnCalculate` 引数 high/low[] の供給）、アラート、
    最適化入力、リアルタイム差分再計算（バッチ全件計算で代替。ガイド §3/§6）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fitHLBand.mq4`（PRO!fit, Copyright 2015,
  PRO!fit Investars）。MQL4。
- 種別: `#property indicator_separate_window`、`indicator_buffers 1`、`indicator_color1
  clrLime`、`indicator_levelcolor C'84,84,84'`、`indicator_levelstyle STYLE_SOLID`、
  `indicator_height 100`、`indicator_width1 2`。
- バッファ/プロット: `ExtVOLBuffer`（プロット 0, **`SetIndexStyle(0, DRAW_HISTOGRAM)`**,
  clrLime, width2, separate window）= `hl_range`。
- σ 水準線: `funIndicatorSet` が `IndicatorSetDouble(INDICATOR_LEVELVALUE, i, ...)` で
  `StcLCStdDevArray[1..4]`（= b165/b196/b258/avg）を水準値に設定（グレー, SOLID）。
- overlay 8 本: `ObjectCreate(..., OBJ_TREND, 0, Time[1], StDev[k], Time[0], StDev[k])`
  ×8（実質水平）、`OBJPROP_COLOR LimeGreen`。
- **input パラメータ: なし**（元コードに `input` 宣言が存在しない）。
- 時系列の向き: `ArraySetAsSeries` 明示なし。`iHigh(NULL,0,0)` / `iLow(NULL,0,0)` は
  shift=0=最新足。本移植は**昇順（古→新）**で扱い、最新足 = 末尾（`high[-1]` / `low[-1]`）。
  平均・母σは全系列のため向きに依らず不変。
- 使用する標準/ライブラリ関数:
  - `iMAOnArray(ExtVOLBuffer,0,rates_total,0,MODE_SMA,0)` … 配列全長 SMA（= 全系列平均）。
  - `iBandsOnArray(ExtVOLBuffer,0,rates_total,dev,0,1,0)` … 全長 SMA ± dev×**母**標準偏差
    （MODE_UPPER=1）。dev ∈ {1.65, 1.96, 2.58}。
  - `iHigh(NULL,0,0)` / `iLow(NULL,0,0)` … 最新足の High / Low。

## 4. Input（入力）
- 必須列: `high` / `low`（列名の大小不問）。CSV ローダ `load_ohlc_csv` は
  `open/high/low/close` を必須とする（open/close は本計算には不使用だが OHLC 規約として要求）。
- 時刻列: 描画アダプタは `time`/`date` 列または `DatetimeIndex` を解決に用いる（任意）。
- 前提: 行は時系列昇順。最低 1 行（空入力は ValueError）。欠損なし（NaN 前提処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 レンジ（`compute_range`）
全バー `i`（warm-up なし・NaN なし）:
```
range[i] = high[i] - low[i]
```
元 `ExtVOLBuffer[i] = high[i] - low[i]`（L61）。

### 5.2 統計（`compute_range_stats`, `iMAOnArray` / `iBandsOnArray` 相当）
range 系列全長の算術平均 `avg` と**母**標準偏差 `sigma = sqrt(mean((x-avg)^2))`（÷N）から:
```
avg  = mean(range)               # iMAOnArray MODE_SMA, period=rates_total
b165 = avg + 1.65 * sigma        # iBandsOnArray dev=1.65 MODE_UPPER
b196 = avg + 1.96 * sigma        # iBandsOnArray dev=1.96 MODE_UPPER
b258 = avg + 2.58 * sigma        # iBandsOnArray dev=2.58 MODE_UPPER
```

### 5.3 最新 H/L への投影 8 本（`compute_hl_bands`）
`H_last = high[-1]`（昇順末尾 = iHigh(NULL,0,0)）、`L_last = low[-1]`（= iLow(NULL,0,0)）:
```
high_avg  = H_last - avg      low_avg  = L_last + avg      # 元 StDev[0] / StDev[4]
high_b165 = H_last - b165     low_b165 = L_last + b165     # 元 StDev[1] / StDev[5]
high_b196 = H_last - b196     low_b196 = L_last + b196     # 元 StDev[2] / StDev[6]
high_b258 = H_last - b258     low_b258 = L_last + b258     # 元 StDev[3] / StDev[7]
```
High 側 = **減算**（最新 High から下へ投影）、Low 側 = **加算**（最新 Low から上へ投影）。

### 5.4 subwindow 範囲
```
sub_min = 0.0           # IndicatorSetDouble(INDICATOR_MINIMUM, 0)（L102）
sub_max = b196 * 2      # IndicatorSetDouble(INDICATOR_MAXIMUM, StcLCStdDevArray[2]*2)（L103）
```
`StcLCStdDevArray[2] = iBandsOnArray(...,1.96,...) = b196` のため `sub_max = b196*2`。

### 5.5 丸め・補間方式
- 元コードに `NormalizeDouble` は無く、float 精度で実装（ガイド §4.1、int 切り捨ても持ち込まない）。
- 標準偏差は母分散ベース（÷N, MT4 iBands 準拠）。

## 6. Entities / 成果物（出力データ）
`build_hlband` の DataFrame（index=入力 index）:
| 列 | 意味 |
|---|---|
| `hl_range`（`RANGE_COLUMN`） | レンジ `high-low`（描画対象, ヒストグラム）。warm-up/NaN なし。 |

スカラ参照値は時系列ではなく価格軸の水平参照値のため成果物 DataFrame と分離し辞書で提供:
- `hlband_levels` → `{avg, b165, b196, b258, sub_min(=0.0), sub_max(=b196*2)}`（separate）。
- `hlband_price_bands` → `{high_avg, high_b165, high_b196, high_b258, low_avg, low_b165,
  low_b196, low_b258}`（overlay 8 本）。

EMPTY_VALUE 相当の非描画点は本指標では発生しない（range は全バー定義）。

## 7. Output（描画）
- 二系統: (A) separate window ＋ (B) main chart overlay。
- matplotlib（`plot_hlband`）: 1 図 2 段。上段=価格（High/Low/Close 参考）＋ overlay 水平線
  8 本（LimeGreen）。下段=`hl_range` ヒストグラム（clrLime）＋ σ 水準線 4 本（avg/b165/b196/
  b258, SOLID, グレー C'84,84,84'）。下段 y 範囲 sub_min(=0)〜sub_max(=b196*2)。
- lightweight-charts:
  - `add_hlband_separate`（subchart）: `create_histogram(name="hl_range")`（clrLime,
    price_line/label=False）＋ σ 水準線 4 本を `horizontal_line`（SOLID, グレー）。
    subwindow 範囲（MIN=0 / MAX=b196*2）は `hlband_levels` の `sub_min`/`sub_max` で提供し、
    呼び出し側が subchart のスケール設定に用いる（実 `lightweight_charts` の
    `create_histogram` は sub_min/sub_max 引数を持たないため独自 kwargs を渡さない）。
  - `add_hlband_overlay`（メイン chart）: `horizontal_line` ×8（high_*/low_*, LimeGreen,
    price_line/label=False）。
  - ヒストグラム値列名は値列名（`hl_range`）と完全一致（ガイド §5）。多数線のため
    price_line/price_label=False（ガイド §6）。塗り不使用。

## 8. Exception（異常系）
- high/low 列欠落: `KeyError`（`build_hlband` / `hlband_levels` / `hlband_price_bands` /
  `add_hlband_separate` / `add_hlband_overlay`）。
- **空 DataFrame（0 行）: `ValueError`**（成果物層 `_extract_hl` のガード。core を変更せず
  成果物層側で明示。core 単体は空入力で `high[-1]` の IndexError / `mean([])` の nan を返す）。
- CSV 必須列（open/high/low/close）欠落・時刻列欠落: `KeyError`（`load_ohlc_csv`）。
- CSV ファイル不在: `FileNotFoundError`（`load_ohlc_csv`）。
- CSV 空（0 行）: `ValueError`（`load_ohlc_csv`）。
- high/low 長不一致: `ValueError`（`compute_range`）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（`lwc_chart`）。

## 9. 元 MQL からの差分

### 一致を保証する点（原挙動の 1:1 再現）
- `range = high - low`（全バー、warm-up なし）。
- 平均 = 全系列 SMA、σ 帯 = `平均 + {1.65,1.96,2.58}×母標準偏差`（÷N）。
- 8 投影の符号: **High 側 = 最新 High からの減算 / Low 側 = 最新 Low への加算**。
- 最新 H/L = 昇順末尾（`high[-1]` / `low[-1]` = iHigh(0)/iLow(0)）。
- `sub_min = 0.0` / `sub_max = b196*2`。

### 意図的に変えた / 前提化した点（根拠）
1. **母σ÷N の 1:1 再現**: `iBandsOnArray` の標準偏差は母分散（÷N）であり、標本σ（ddof=1）を
   使わない。`compute_range_stats` は `sqrt(mean((x-avg)^2))` で再現する。テストは
   discriminating input（母σ と標本σ で値が異なる系列）で固定する。
2. **MT4 描画オブジェクト（OBJ_TREND）を水平線で再表現**: 元は `ObjectCreate(OBJ_TREND,
   Time[1]→Time[0])` の実質水平トレンドラインを 8 本生成し、毎ティック `ObjectDelete` →
   `ObjectCreate` で更新する。移植先（matplotlib `axhline` / lwc `horizontal_line`）は
   オブジェクトのライフサイクル管理を持たず、投影値 8 本のみを水平線として描く（SPEC §2）。
3. **母σ統計の共有 common 化は別タスク（rule-of-three）**: range の全系列「平均 ±dev×母σ」帯
   という統計パターンは `profit_adx_needle`・`profit_stc` に続き本指標で**3 例目**となる。
   rule-of-three により共有 `common/` への抽出が妥当な段階に到達したが、本タスクのスコープは
   profit_hlband の完成に限定されるため**共有化は別タスクとして提案**し、現状は in-package
   （`src/core.py` 内）に留める（YAGNI／スコープ限定）。共有層は描画・pandas 非依存の numpy
   関数として昇格できるよう core はシグネチャ独立を維持している。
4. **`int` 切り捨て・`NormalizeDouble` は持ち込まない**（ガイド §4.1）。float 精度で実装。
5. **input パラメータなし**: 元コードに `input` が存在しないため、移植先にもパラメータを
   設けない（period 等の引数を追加しない）。
6. **空入力ガードの新設**: 元 MQL4 はチャート供給データが常に 1 本以上ある前提で空入力を
   想定しない。移植先は CSV 等任意入力を受けるため、最新 H/L 投影・統計が定義不能となる空
   DataFrame に対し成果物層で明示的 `ValueError` を投げる（core は不変）。

### bit-exact について
- **MT4 純正との bit-exact は非保証**: MT4 実機の参照 CSV（iMAOnArray/iBandsOnArray 出力 /
  最終投影 CSV）が無いため、浮動小数の最終桁は実機と厳密一致しない可能性がある。完全一致が
  必要な場合は参照 CSV による回帰固定が必要。
