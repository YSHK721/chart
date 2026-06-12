# PRO!fit_Arctan 移植仕様書

## 1. Objective（目的）
移動平均の隣接差 `MA[i]-MA[i-1]` を `MathArctan` で角度（度）へ変換したオシレーター
`iARCTAN` を 7 種の適用価格で算出し、各値を「系列平均からの標準化距離」へ単位変換して
合算した**符号付きオシレーター（市場の「温度」）**を可視化する。MA の傾き（変化の鋭さ）の
偏差を、0 を基準としたヒストグラムで表現する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（7×iARCTAN→レベルカウント→σ12 水準→±3.29σ クランプ）/ 描画（separate
  window のヒストグラム 1 本 + σ12 水準線）/ 入力（CSV → OHLC）。
- **PS.mqh 依存**: レベルカウント集計（`PS_GetLevelCountValue` / `PS_GetUnitConversion` /
  `PS_GetAverage` / `PS_GetStandardDeviationValue`）と σ 水準（`iBandsOnArray`）は
  `ProfitSystem/PS.mqh` に依存する。本移植では当該関数を core 層に移す。
- 対象外: ブローカー接続・チャートデータ供給、アラート、最適化入力、リアルタイム差分
  再計算（バッチ全件計算で代替。ガイド §3/§6）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fit_Arctan.mq4`（Copyright 2016, PRO!fit System
  Investars）。依存: `sample/MQL4/Include/ProfitSystem/PS.mqh`（`iARCTAN`）。
- 種別: MQL4。`#property indicator_separate_window`、`indicator_buffers 1` /
  `indicator_plots 1`。バッファはクランプ済みレベルカウント（**`DRAW_HISTOGRAM`**,
  `#property indicator_color1 DarkGreen`, **height 100**）。σ 水準線は
  `indicator_levelcolor C'84,84,84'`, `indicator_levelstyle STYLE_SOLID`。
- input パラメータ:
  - `int inpPeriod = 6`（MA 期間）。
  - `int inpTypeMA = 1`（MA 方式。**1=EMA** が既定）。
  - `double BarWidth = 0.1`（iARCTAN の角度スケール除数）。
- 時系列の向き: 本移植は**昇順（古い→新しい）**で扱い、MA / EMA / 標準偏差 / 平均を昇順で
  計算する（ガイド §4.3）。
- 使用する標準/ライブラリ関数:
  - `iMA(..., applied_price, shift)` … MA 本体（方式は `inpTypeMA`）。
  - `MathArctan` … iARCTAN の角度化。
  - `iBandsOnArray(arr,0,length,deviation,0,{1|2},0)` … 配列全長 SMA ± deviation×母標準偏差。
  - `PS_GetLevelCountValue` / `PS_GetUnitConversion` / `PS_GetAverage` /
    `PS_GetStandardDeviationValue`（`iStdDevOnArray(...,MODE_EMA,...)`）/
    `PS_IndicatorLevelValueSet`。

## 4. Input（入力）
- 必須列: `open` / `high` / `low` / `close`（列名の大小不問）。**ADX_NEEDLE と異なり
  `open` も計算に入る**（7 適用価格に O が含まれ、MA 入力を切り替えるため）。CSV ローダは
  `open/high/low/close` を必須とする。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 iARCTAN（`compute_arctan`, period=6, ma_method=1, bar_width=0.1）
単一の適用価格系列 `price` に対し `ma = MA(price, period, ma_method)`（昇順, on_buffer で
buffer 0 を初期化→破壊更新）を求め、各バー i:
```
A = ma[i] ; B = ma[i-1]
if i==0 or B==0:  iARCTAN[i] = 0          # 元 double_B==NULL（B 未確定）→ 0
else:             iARCTAN[i] = (atan(A-B) / bar_width) * (180 / 3.14159265359)
```
`B 未確定（warm-up / i==0）→ 0` を 1:1 再現する。角度定数 π は元コードと同じ
`3.14159265359` を用いる。

### 5.2 適用価格（7 系統）
適用価格は**共有層 `common`**（`AppliedPrice` / `applied_price`）から供給する。処理順は
元 `OnCalculate` 呼び出し順に従い:
```
W(WEIGHTED) → T(TYPICAL) → M(MEDIAN) → H(HIGH) → L(LOW) → O(OPEN) → C(CLOSE)
```
**CLOSE=1 系**（common の列挙）。PS.mqh のコメント（applied_price 0-6）は `iMARD` 専用の
記述であり本指標では不採用。MA 方式 `inpTypeMA` の 0-3 は `iMA` の MODE 写像:
```
0=SMA / 1=EMA / 2=SMMA / 3=LWMA
```
`digits`（PS.mqh の iARCTAN 引数）は本移植で**捨て引数**（計算に未使用）。

### 5.3 レベルカウント（`compute_level_count`）
7 系統それぞれの iARCTAN 系列について、平均 `avg`（算術平均, 丸め 5 桁）と EMA 基準標準偏差
`std = sqrt(mean((arc - EMA(arc,length)_last)^2))` を求め、各 i:
```
sigma = 3.29 (SIGMA_L6) ; distant = 329 (PS_SIGMA_DISTANCE_L6)
band_up   = round(avg + std*sigma, 5)
band_down = round(avg - std*sigma, 5)
if arc[i] > avg:   res[i] += round( ((arc[i]-avg)/((band_up  -avg)/distant))/100 , 5)
elif arc[i] < avg: res[i] += round( ((avg-arc[i])/((band_down-avg)/distant))/100 , 5)
else:              res[i]  = 0
```
これを 7 回（**1 系統目 W のみ初期化** `initialization=True`、残り 6 系統は加算）。
iARCTAN は適用価格ごとに MA 入力が異なり 7 系統が別系列となるため、合算は単純な 7 倍では
なく 7 系統の単位変換値の総和となる（**符号付き**：平均超で正・平均未満で負）。

### 5.4 σ12 水準（`compute_sigma_levels` = `compute_arctan_levels`, `iBandsOnArray` 相当）
レベルカウント全長の `mean` と母標準偏差 `popstd=sqrt(mean((x-mean)^2))` から、各
σ∈{0.67,1.28,1.65,1.96,2.58,3.29} について `up_*=round(mean+σ·popstd,5)` /
`dn_*=round(mean-σ·popstd,5)`（上方 6 本 + 下方 6 本＝**σ12**）。

### 5.5 クランプ（出力ヒストグラム）
`arctan_lc = clip(level_count, dn_329, up_329)`（±3.29σ）。

### 5.6 丸め・補間方式
- `NormalizeDouble(_,5)` → `round(x,5)`。平均・EMA 標準偏差バンド・単位変換結果・σ 水準に適用。
- EMA は 2/(N+1)。標準偏差は母分散ベース（÷N）。

## 6. Entities / 成果物（出力データ）
`build_arctan` の DataFrame（index=入力 index）:
| 列 | 意味 |
|---|---|
| `arctan_lc`（`LEVEL_COUNT_COLUMN`） | ±3.29σ クランプ後のヒストグラム値（描画対象）。符号付き。 |

`compute_arctan_full` の DTO `ArctanResult`:
| 属性 | 意味 |
|---|---|
| `level_count_clamped` | 描画対象（クランプ済み）。 |
| `raw_level_count` | クランプ前レベルカウント。 |
| `levels` | σ12 水準（`up_067..up_329` / `dn_067..dn_329`）。 |

σ12 水準線は `arctan_levels`（`up_067..up_329` / `dn_067..dn_329`）でスカラ提供
（時系列ではないため成果物 DataFrame と分離）。全バー算出のため EMPTY_VALUE 相当の
非描画点は発生しない。

## 7. Output（描画）
- 別ウィンドウ（separate window）型。
- matplotlib（`src/plot.py`）: 下段ペインに棒ヒストグラム（DarkGreen `#006400`, 0 基準線あり,
  height 相当）+ σ12 水準線（点線, `#545454` = C'84,84,84'）。`use("Agg")` でヘッドレス。
- lightweight-charts（`src/lwc_chart.py`）: `create_histogram`（名前 `arctan_lc`, DarkGreen,
  price_line=False / price_label=False）+ σ12 水準線を `horizontal_line`（点線, **12 本**）。
  値列名はヒストグラム名 `arctan_lc` と完全一致。`lightweight_charts` を import せず
  duck typing で受ける。

## 8. Exception（異常系）
- OHLC 列欠落: `KeyError`（`build_arctan` / loader / lwc_chart）。
- OHLC 長不一致: `ValueError`（`compute_arctan_full`）。
- `period<2`: `ValueError`（`compute_arctan`）。
- 未知の `ma_method`: `ValueError`（`compute_arctan`）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（lwc_chart）。

## 9. 元 MQL からの差分

### 一致を保証する点
- iARCTAN 算式（`(atan(MA[i]-MA[i-1])/bar_width)*(180/π)`、π=3.14159265359）を 1:1 再現。
- **B 未確定（warm-up / i==0）→ 0** を 1:1 再現。
- レベルカウントの符号（平均超=正/未満=負）、単位変換式、7 回加算（W のみ初期化）、
  `NormalizeDouble(_,5)` の適用箇所。σ バンド（SMA±σ·母std）とクランプ（±3.29σ）。
- 7 適用価格で MA 入力が異なる点（iARCTAN は applied_price が実際に計算へ入る）。

### 意図的に変えた / 前提化した点（根拠）
1. **`ps_level_count` / `compute_sigma_levels` は共有層 `profit_system` へ集約済み**: 同一
   PS.mqh 関数（`PS_GetLevelCountValue` / `PS_GetUnitConversion` / `iBandsOnArray`）を
   再現する共有実装を import 再公開する（profit_adx_needle と同一実装を参照。複製による
   乖離を排除）。
2. **`iARCTAN` / `compute_level_count` は新規**（ADX_NEEDLE には無い iARCTAN オシレーター）。
   `iARCTAN` は元 PS.mqh の **8 引数**（symbol/timeframe/period/ma_method/applied_price/
   bar_width/shift/**digits**）のうち `digits` を**無視**（捨て引数）し、B 未確定→0 を 1:1 再現。
3. **applied_price は計算へ入る（ADX_NEEDLE との対比）**: ADX_NEEDLE では applied_price が
   vestigial（同一 ADX を 7×）だったが、iARCTAN では MA 入力を切り替えるため 7 系統が
   別系列となる。`applied_price` は common から供給（CLOSE=1 系。PS.mqh の 0-6 コメントは
   `iMARD` 専用で不採用）。MA 方式 `inpTypeMA` 0-3 を `iMA` MODE へ写像。
4. **ビット完全一致は非保証**: MT4 実機の参照値（各適用価格の iARCTAN 出力 / 最終ヒストグラム
   CSV）が無いため、EMA 初期化（先頭バー seed）や warm-up 区間は実機と厳密一致しない可能性が
   ある。完全一致が必要な場合は参照 CSV による回帰固定が必要。
5. **`int` 切り捨ては持ち込まない**（ガイド §4.1）。float 精度で実装。
