# PRO!fit_Volatility 移植仕様書

## 1. Objective（目的）
現足の価格 X と `period` 本前の価格 Y の乖離 `iVOLATILITY = pX[a] - pY[a-period]` を、
適用価格 X × Y の **49 系列**（X∈0..6 × Y∈0..6 = price_A × price_B）で算出し、各値を
「系列平均からの標準化距離（σ 距離）」へ単位変換して合算した **符号付きオシレーター
（価格乖離の「温度」）** を可視化する。49 系列の価格乖離の偏差を、0 を基準とした
ヒストグラムで表現する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（49×iVOLATILITY→レベルカウント→σ12 水準→±3.29σ クランプ）/ 描画
  （separate window のヒストグラム 1 本 + σ12 水準線）/ 入力（CSV → OHLC）。
- **PS.mqh 依存**: レベルカウント集計（`PS_GetLevelCountValue` / `PS_GetUnitConversion` /
  `PS_GetAverage` / `PS_GetStandardDeviationValue`）と σ 水準（`iBandsOnArray`）は
  `ProfitSystem/PS.mqh` に依存する。本移植では当該関数を core 層に移す。`iVOLATILITY`
  本体（49 系列の `pX[a]-pY[a-period]`）も PS.mqh 由来であり core 層に移す。
- 対象外: ブローカー接続・チャートデータ供給、アラート、最適化入力、`iFileWrite()`、
  `last_calc` による差分更新（バッチ全件計算で代替。ガイド §3/§6）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fit_Volatility.mq4`（Copyright 2017, PRO!fit
  System Investars）。依存: `sample/MQL4/Include/ProfitSystem/PS.mqh`（`iVOLATILITY`）。
- 種別: MQL4。`#property indicator_separate_window`、`#property indicator_height 100`、
  `indicator_buffers 13`（描画は 1）/ `indicator_plots 1`。描画バッファ 0 はクランプ済み
  レベルカウント（**`DRAW_HISTOGRAM`**, `#property indicator_color1 DarkGreen`,
  **height 100**, `indicator_width1 1`）。残り 12 バッファは σ 水準線の表示用
  （`StdDevArray[1..6]`=上方 / `[7..12]`=下方）。σ 水準線は
  `indicator_levelcolor C'84,84,84'`, `indicator_levelstyle STYLE_SOLID`。
- input パラメータ:
  - `int inpPeriod = 6`（乖離をとる足数）。
  - `string inpSymbol = NULL` / `int inpTimeFrame = 0`（シンボル・時間足。本移植は
    供給データに対して計算するため未使用＝捨て引数）。
- 時系列の向き: 本移植は **昇順（古い→新しい）** で扱い、MA（EMA）/ 標準偏差 / 平均を
  昇順で計算する（ガイド §4.3）。元の MT4 shift（新しい足ほど小 index）を
  `res[a]=pX[a]-pY[a-period]` に直して再現する。
- 使用する標準/ライブラリ関数:
  - `iVOLATILITY(symbol,timeframe,period,XY,shift)`（PS.mqh）… `pX[shift]-pY[shift+period]`。
  - `iBandsOnArray(arr,0,length,deviation,0,{1|2},0)` … 配列全長 SMA ± deviation×母標準偏差。
  - `PS_GetLevelCountValue` / `PS_GetUnitConversion` / `PS_GetAverage` /
    `PS_GetStandardDeviationValue`（`iStdDevOnArray(...,MODE_EMA,...)`）/
    `PS_IndicatorLevelValueSet`。

## 4. Input（入力）
- 必須列: `open` / `high` / `low` / `close`（列名の大小不問）。**`volume` 不要**。
  iVOLATILITY の digit 1=Open が計算に入るため `open` を必須とする。CSV ローダ
  （`load_ohlc_csv`）は open/high/low/close を必須とする。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 iVOLATILITY（`compute_volatility`, period=6, x_digit, y_digit）
mode は 2 桁コード `XY`（X=1 桁目=price_A=現足側 x_digit、Y=2 桁目=price_B=period 本前側
y_digit、各 0..6）。単一の (x_digit, y_digit) に対し:
```
pX = price(x_digit) ; pY = price(y_digit)    # 各 OHLC 系列（昇順）
for a in 0..n-1:
    if a < period:  res[a] = 0               # warm-up（未計算）
    else:           res[a] = pX[a] - pY[a-period]
```
`a < period` の warm-up は元 `OnCalculate` のループ `for(i=0; i<limit-inpPeriod; i++)` が
最古 `period` 本を計算せず `ArrayResize` 既定値 0 を残す挙動を **1:1 再現**（ISSUE-002
解決済）。`period < 2` は `ValueError`。

### 5.2 適用価格（digit 0..6・iVOLATILITY 素の MT4 式）
```
0=Close, 1=Open, 2=High, 3=Low,
4=Median   = (H+L)/2,
5=Typical  = (H+L+C)/3,
6=Weighted = (O+H+L+C)/4
```
**WEIGHTED=(O+H+L+C)/4** は iVOLATILITY 固有の式であり、共有層 `common.applied_price` の
標準 weighted=(H+L+2C)/4 と **異なる**ため、本 core は iVOLATILITY の式をそのまま実装する。
digit が 0..6 外は `ValueError`。49 系列の出現順（`VOLATILITY_MODES`）は元 `OnCalculate`
の case 順 `00,01..06,10..16,...,60..66`（X 外側ループ・Y 内側ループ）。

### 5.3 レベルカウント（`compute_level_count`）
49 系列それぞれの iVOLATILITY 系列について、平均 `avg`（算術平均, 丸め 5 桁）と EMA 基準
標準偏差 `std = sqrt(mean((vol - EMA(vol,length)_last)^2))` を求め、各 i:
```
sigma = 3.29 (SIGMA_L6) ; distant = 329 (PS_SIGMA_DISTANCE_L6)
band_up   = round(avg + std*sigma, 5)
band_down = round(avg - std*sigma, 5)
if vol[i] > avg:   res[i] += round( ((vol[i]-avg)/((band_up  -avg)/distant))/100 , 5)
elif vol[i] < avg: res[i] += round( ((avg-vol[i])/((band_down-avg)/distant))/100 , 5)
else:              res[i]  = 0
```
これを 49 回（**1 系統目 mode 00（X=0,Y=0）のみ初期化** `initialization=True`、残り 48 系統は
加算）。元 `OnCalculate` は mode 00 の `PS_GetLevelCountValue` 第 1 引数のみ 1（init）、残り 48 は
0（加算）。合算は単純な 49 倍ではなく 49 系統の単位変換値の総和（**符号付き**：平均超で正・
平均未満で負）。

### 5.4 σ12 水準（`compute_sigma_levels` = `compute_volatility_levels`, `iBandsOnArray` 相当）
レベルカウント全長の `mean` と母標準偏差 `popstd=sqrt(mean((x-mean)^2))` から、各
σ∈{0.67,1.28,1.65,1.96,2.58,3.29} について `up_*=round(mean+σ·popstd,5)` /
`dn_*=round(mean-σ·popstd,5)`（上方 6 本＝`StdDevArray[1..6]` + 下方 6 本＝
`StdDevArray[7..12]`＝**σ12**）。

### 5.5 クランプ（出力ヒストグラム）
`volatility_lc = clip(level_count, dn_329, up_329)`（±3.29σ）。元 `OnCalculate` 末尾の
`if(>SD_1S6) =SD_1S6; else if(<SD_2S6) =SD_2S6;` を再現。

### 5.6 丸め・補間方式
- `NormalizeDouble(_,5)` → `round(x,5)`。平均・EMA 標準偏差バンド・単位変換結果・σ 水準に適用。
- EMA は 2/(N+1)。標準偏差は母分散ベース（÷N）。

## 6. Entities / 成果物（出力データ）
`build_volatility` の DataFrame（index=入力 index）:
| 列 | 意味 |
|---|---|
| `volatility_lc`（`LEVEL_COUNT_COLUMN`） | ±3.29σ クランプ後のヒストグラム値（描画対象）。符号付き。 |

`compute_volatility_full` の DTO `VolatilityResult`:
| 属性 | 意味 |
|---|---|
| `level_count_clamped` | 描画対象（クランプ済み）。 |
| `raw_level_count` | クランプ前レベルカウント。 |
| `levels` | σ12 水準（`up_067..up_329` / `dn_067..dn_329`）。 |

σ12 水準線は `volatility_levels`（`up_067..up_329` / `dn_067..dn_329`）でスカラ提供
（時系列ではないため成果物 DataFrame と分離）。全バー算出のため EMPTY_VALUE 相当の
非描画点は発生しない。

## 7. Output（描画）
- 別ウィンドウ（separate window）型、height 100。
- matplotlib（`src/plot.py` `plot_volatility`）: 下段ペインに棒ヒストグラム
  （DarkGreen `#006400`, 0 基準線あり）+ σ12 水準線（点線, `#545454` = C'84,84,84'）。
  `use("Agg")` でヘッドレス。
- lightweight-charts（`src/lwc_chart.py` `add_volatility`）: `create_histogram`
  （名前 `volatility_lc`, DarkGreen, price_line=False / price_label=False）+ σ12 水準線を
  `horizontal_line`（点線, **12 本**）。値列名はヒストグラム名 `volatility_lc` と完全一致。
  `lightweight_charts` を import せず duck typing で受ける。

## 8. Exception（異常系）
- OHLC 列欠落: `KeyError`（`build_volatility` / `load_ohlc_csv` / `add_volatility`）。
- OHLC 長不一致: `ValueError`（`compute_volatility_full`）。
- `period<2`: `ValueError`（`compute_volatility`）。
- 未知の price digit（0..6 外）: `ValueError`（`_vol_price`）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（`add_volatility`）。
- CSV ファイル不在: `FileNotFoundError`（`load_ohlc_csv`）。

## 9. 元 MQL からの差分

### 一致を保証する点
- iVOLATILITY 算式 `res[a]=pX[a]-pY[a-period]` を 49 系列（X∈0..6 × Y∈0..6, X=price_A 現足 /
  Y=price_B period 本前）で 1:1 再現。
- **iVOLATILITY の warm-up（最古 `period` 本, `a<period`）は 0**。元 `OnCalculate` の
  `for(i=0; i<limit-inpPeriod; i++)` が当該区間を未計算のまま 0 を残す挙動を **1:1 再現**
  （**ISSUE-002 解決済**）。
- レベルカウントの符号（平均超=正/未満=負）、単位変換式、49 回加算（**mode 00 のみ初期化**、
  残り 48 系統は加算）、`NormalizeDouble(_,5)` の適用箇所。σ バンド（SMA±σ·母std）と
  クランプ（±3.29σ）。

### 意図的に変えた / 前提化した点（根拠）
1. **`WEIGHTED=(O+H+L+C)/4` は iVOLATILITY 固有式**であり、共有層 `common.applied_price` の
   標準 weighted=(H+L+2C)/4 と **異なる**。本 core は iVOLATILITY の素の MT4 式
   （median=(H+L)/2, typical=(H+L+C)/3, weighted=(O+H+L+C)/4）をそのまま実装する。
2. **`ps_level_count` / `compute_sigma_levels` は共有層 `profit_system` へ集約済み**:
   同一 PS.mqh 関数（`PS_GetLevelCountValue` / `PS_GetUnitConversion` /
   `iBandsOnArray`）を再現する共有実装を import 再利用する（profit_adx_needle と
   同一実装を参照。複製による乖離を排除）。
3. **`iVOLATILITY` / `compute_level_count` は新規**（ADX_NEEDLE / Arctan には無い 49 系列の
   価格乖離オシレーター）。`inpSymbol` / `inpTimeFrame` は本移植で捨て引数（供給データに
   対して計算）。`iFileWrite()` / `last_calc` 差分更新は対象外（バッチ全件計算で代替）。
4. **ビット完全一致は非保証**: MT4 実機の参照値（各系列の iVOLATILITY 出力 / 最終ヒストグラム
   CSV）が無いため、EMA 初期化（先頭バー seed）や warm-up 区間は実機と厳密一致しない可能性が
   ある。完全一致が必要な場合は参照 CSV による回帰固定が必要。
5. **`int` 切り捨ては持ち込まない**（ガイド §4.1）。float 精度で実装。
