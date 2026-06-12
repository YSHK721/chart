# PRO!fit_Oscillator 移植仕様書

## 1. Objective（目的）
18 本のサブオシレーター系列（iRSI×7 / iStochastic×2 / iMFI×1 / iRVI×1 / iMARD×7）を
それぞれ「系列平均からの標準化距離（σ 距離）」へ単位変換して合算した **符号付き複合
オシレーター（市場の「温度」）** を可視化する。複数オシレーターの偏差を 1 本のヒスト
グラムへ集約し、0 を基準として市場の過熱/過冷を表現する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（18 サブ系列 → レベルカウント → σ12 水準 → ±3.29σ クランプ）/ 描画
  （separate window のヒストグラム 1 本 + σ12 水準線）/ 入力（CSV → OHLCV, **volume 必須**）。
- **PS.mqh 依存**: レベルカウント集計（`PS_GetLevelCountValue` / `PS_GetUnitConversion` /
  `PS_GetAverage` / `PS_GetStandardDeviationValue`）、σ 水準（`iBandsOnArray`）、および
  サブ系列のうち `iRVI` / `iMARD` は `ProfitSystem/PS.mqh` に依存する。本移植では当該
  関数を core 層へ移す。
- 対象外: ブローカー接続・チャートデータ供給、アラート、最適化入力、リアルタイム差分
  再計算（バッチ全件計算で代替。ガイド §3/§6）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fit_Oscillator.mq4`（Copyright 2016, PRO!fit System
  Investars）。依存: `sample/MQL4/Include/ProfitSystem/PS.mqh`（`iRVI` / `iMARD` /
  `PS_GetLevelCountValue` 等）。
- 種別: MQL4。`#property indicator_separate_window`、`indicator_buffers 1` /
  `indicator_plots 1`。バッファはクランプ済みレベルカウント（**`DRAW_HISTOGRAM`**,
  `#property indicator_color1 DarkGreen`, **height 100**）。σ 水準線は
  `indicator_levelcolor C'84,84,84'`, `indicator_levelstyle STYLE_SOLID`。
- input パラメータ:
  - `int inpPeriodA = 6`（オシレーター期間。iRSI / iStochastic / iMFI / iRVI）。
  - `int inpPeriodB = 60`（iMARD の EMA 期間）。
- 時系列の向き: 本移植は **昇順（古い→新しい）** で扱い、MA / EMA / 標準偏差 / 平均を昇順で
  計算する（ガイド §4.3）。
- 使用する標準/ライブラリ関数:
  - `iRSI(...,applied_price)` × 7 / `iStochastic(...,1,1,1,MODE_MAIN|SIGNAL,0)` × 2 /
    `iMFI(...)` × 1 / `iRVI(...,MODE_MAIN)` × 1 / `iMARD(...,MODE_EMA,applied_price)` × 7。
  - `iBandsOnArray(arr,0,length,deviation,0,{1|2},0)` … 配列全長 SMA ± deviation×母標準偏差。
  - `PS_GetLevelCountValue` / `PS_GetUnitConversion` / `PS_GetAverage` /
    `PS_GetStandardDeviationValue`（`iStdDevOnArray(...,MODE_EMA,...)`）/
    `PS_IndicatorLevelValueSet`。

## 4. Input（入力）
- 必須列: `open` / `high` / `low` / `close` / **`volume`**（列名の大小不問）。
  - `volume`: iMFI が出来高を要する（先例 profit_mfi 踏襲）。
  - `open`: iRSI / iMARD の 7 適用価格（W/T/M/H/L/O/C に O を含む）と iRVI（close−open）に入る。
  - CSV ローダ `load_ohlcv_csv` は open/high/low/close/volume を必須とする。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 サブ系列（18 系列・処理順厳守）
処理順は元 `OnCalculate`（L168-256）の `PS_GetLevelCountValue` 呼び出し順に従う:
```
IC01: iRSI × 7   （適用価格 W→T→M→H→L→O→C, period_a）  ← IC01_W が initialization
IC02: iStochastic × 2 （MAIN→SIGNAL, period_a）         ← 両者とも生 %K（同一配列を 2 回加算）
IC03: iMFI × 1   （period_a, volume 使用）
IC04: iRVI × 1   （period_a, MAIN, 三角加重）
IC05: iMARD × 7  （適用価格 W→T→M→H→L→O→C, period_b, EMA）
```
- **iRSI**（`compute_rsi`, period_a=6）: 共有 mql_builtins の再公開。warm-up 0、
  Wilder 平滑、`neg==0 & pos==0 → 50`。
- **iStochastic**（`compute_stochastic`, period_a=6）: 共有 mql_builtins の再公開。
  直近 period 本の高安レンジに対する終値位置 %K（生・fast）。MAIN / SIGNAL とも
  slowing=1 / Dperiod=1 のため **生 %K に帰着**＝同一 %K 配列を 2 回加算。range==0 → 0。
- **iMFI**（`compute_mfi`, period_a=6）: 共有 mql_builtins の再公開。Typical price
  `(H+L+C)/3` × volume の正/負マネーフロー比。`neg==0 → 100`。
- **iRVI**（`compute_rvi`, period_a=6, MAIN）: 権威 `RVI.mq5` を 1:1 再現。各バー j:
  `value_up[j] = (c-o)[j] + 2(c-o)[j-1] + 2(c-o)[j-2] + (c-o)[j-3]`（三角加重 1,2,2,1）、
  `value_down[j] = (h-l)[j] + 2(h-l)[j-1] + 2(h-l)[j-2] + (h-l)[j-3]`。バー i は窓 `[i-period+1..i]`
  の和 `sum_up / sum_down`（`sum_down==0 → sum_up`）。**warm-up i<period+2 は 0**（最初の実値は i=period+2＝権威 RVI.mq5 の start）、j-3<0 は除外。
- **iMARD**（`compute_mard`, period_b=60, MODE_EMA, applied_price）: 元 PS.mqh `iMARD` を再現。
  `res[i] = (num_price[i] − ma[i]) / ma[i]`、`ma = EMA(applied_price(applied), period)`、
  `ma[i]==0 → res[i]=0`（退化ガード。元はガード無いが warm-up EMA は非 0 のため 0 のみ回避）。

### 5.2 適用価格（IC01 / IC05 の 7 系統）
適用価格は **共有層 `common`**（`AppliedPrice` / `applied_price`）から供給する。処理順:
```
W(WEIGHTED) → T(TYPICAL) → M(MEDIAN) → H(HIGH) → L(LOW) → O(OPEN) → C(CLOSE)
```
**CLOSE=1 系**（common の列挙）。

### 5.3 レベルカウント（`compute_level_count` / `ps_level_count`）
18 系列それぞれについて、平均 `avg`（算術平均, 丸め 5 桁）と EMA 基準標準偏差
`std = sqrt(mean((s − EMA(s,length)_last)^2))` を求め、各 i:
```
sigma = 3.29 (SIGMA_L6) ; distant = 329 (PS_SIGMA_DISTANCE_L6)
band_up   = round(avg + std*sigma, 5)
band_down = round(avg - std*sigma, 5)
if s[i] > avg:   res[i] += round( ((s[i]-avg)/((band_up  -avg)/distant))/100 , 5)
elif s[i] < avg: res[i] += round( ((avg-s[i])/((band_down-avg)/distant))/100 , 5)
else:            res[i]  = 0
```
これを 18 回（**1 系統目 iRSI_WEIGHTED のみ初期化** `initialization=True`、残り 17 系統は加算）。
**符号付き**（平均超で正・平均未満で負）の合算となる。

### 5.4 σ12 水準（`compute_sigma_levels` = `compute_oscillator_levels`, `iBandsOnArray` 相当）
レベルカウント全長の `mean` と母標準偏差 `popstd=sqrt(mean((x-mean)^2))` から、各
σ∈{0.67,1.28,1.65,1.96,2.58,3.29} について `up_*=round(mean+σ·popstd,5)` /
`dn_*=round(mean-σ·popstd,5)`（上方 6 本 + 下方 6 本＝**σ12**）。

### 5.5 クランプ（出力ヒストグラム）
`oscillator_lc = clip(level_count, dn_329, up_329)`（±3.29σ）。

### 5.6 丸め・補間方式
- `NormalizeDouble(_,5)` → `round(x,5)`。平均・EMA 標準偏差バンド・単位変換結果・σ 水準に適用。
- EMA は 2/(N+1)。標準偏差は母分散ベース（÷N）。

## 6. Entities / 成果物（出力データ）
`build_oscillator` の DataFrame（index=入力 index）:
| 列 | 意味 |
|---|---|
| `oscillator_lc`（`LEVEL_COUNT_COLUMN`） | ±3.29σ クランプ後のヒストグラム値（描画対象）。符号付き。 |

`compute_oscillator_full` の DTO `OscillatorResult`:
| 属性 | 意味 |
|---|---|
| `level_count_clamped` | 描画対象（クランプ済み）。 |
| `raw_level_count` | クランプ前レベルカウント。 |
| `levels` | σ12 水準（`up_067..up_329` / `dn_067..dn_329`）。 |

σ12 水準線は `oscillator_levels`（`up_067..up_329` / `dn_067..dn_329`）でスカラ提供
（時系列ではないため成果物 DataFrame と分離）。全バー算出のため EMPTY_VALUE 相当の
非描画点は発生しない。

## 7. Output（描画）
- 別ウィンドウ（separate window）型。
- matplotlib（`src/plot.py`）: 下段ペインに棒ヒストグラム（DarkGreen `#006400`, 0 基準線あり,
  height 相当）+ σ12 水準線（点線, `#545454` = C'84,84,84'）。`use("Agg")` でヘッドレス。
  matplotlib 依存を本パッケージ import に持ち込まないため公開 API から除外（`from src.plot
  import plot_oscillator` で個別 import）。
- lightweight-charts（`src/lwc_chart.py`）: `create_histogram`（名前 `oscillator_lc`, DarkGreen,
  price_line=False / price_label=False）+ σ12 水準線を `horizontal_line`（点線, **12 本**）。
  値列名はヒストグラム名 `oscillator_lc` と完全一致。`lightweight_charts` を import せず
  duck typing で受ける。

## 8. Exception（異常系）
- OHLCV 列欠落（**volume 含む**）: `KeyError`（`build_oscillator` / loader / lwc_chart）。
- OHLCV 長不一致: `ValueError`（`compute_oscillator_full`）。
- `period_a<2` / `period_b<2`: `ValueError`（`compute_oscillator_full` / 各サブ計算）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（lwc_chart）。

## 9. 元 MQL からの差分

### 一致を保証する点
- 18 サブ系列の処理順（IC01 W→C → IC02 main→signal → IC03 → IC04 → IC05 W→C）と
  initialization 位置（IC01_W のみ）。
- 各サブ系列の算式（iRSI / iStochastic / iMFI / iRVI / iMARD）を 1:1 再現。
- レベルカウントの符号（平均超=正/未満=負）、単位変換式、`NormalizeDouble(_,5)` の適用箇所。
- σ バンド（SMA±σ·母std）とクランプ（±3.29σ）。
- 7 適用価格で iRSI / iMARD の入力が異なる点（applied_price が実際に計算へ入る）。

### 意図的に変えた / 前提化した点（根拠）
1. **iMARD WEIGHTED の非対称を 1:1 再現（修正しない・§4.4）**: 元 PS.mqh の `iMARD` は
   分子に `(O+H+L+C)/4`（iMARD 独自 WEIGHTED, PS.mqh L1316）、分母 EMA に common 標準
   `WEIGHTED=(H+L+2C)/4` を用いる **非対称** 構造を持つ。これは原コードの挙動であり、
   **修正せず 1:1 再現する**（他 6 適用価格は分子/分母とも対称）。
2. **iRVI は三角加重・warm-up i<period+2 を 1:1 再現**（権威 `RVI.mq5`）。三角加重係数
   1,2,2,1、j-3<0 除外、`sum_down==0 → sum_up` を保持。
3. **iStochastic 2 モードは生 %K に帰着**: 元 `iStochastic(...,1,1,1,MODE_MAIN|SIGNAL,0)` は
   slowing=1 / Dperiod=1 のため MAIN / SIGNAL とも生 %K となり、同一 %K 配列を 2 回加算する。
4. **共有と新規の区分**: `iRSI` / `iMFI` / `iStochastic` は共有 `mql_builtins`、
   `ps_level_count` / `compute_sigma_levels` は共有 `profit_system` を **import 再公開**
   （共有層の正準実装に一本化済み。複製による乖離を排除）。`iRVI` / `iMARD` は
   **新規実装**（ADX_NEEDLE / Arctan には無い）。
5. **applied_price は計算へ入る**: iRSI / iMARD は MA / RSI 入力を 7 適用価格で切り替えるため
   7 系統が別系列となる。`applied_price` は common から供給（CLOSE=1 系。PS.mqh の 0-6
   コメントは `iMARD` 専用の記述で本指標の供給元としては不採用）。
6. **ビット完全一致は非保証**: MT4 実機の参照値（各サブ系列の出力 / 最終ヒストグラム CSV）が
   無いため、EMA 初期化（先頭バー seed）や warm-up 区間は実機と厳密一致しない可能性がある。
   完全一致が必要な場合は参照 CSV による回帰固定が必要。
7. **`int` 切り捨ては持ち込まない**（ガイド §4.1）。float 精度で実装。
