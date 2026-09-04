# PRO!fit_ADX_NEEDLE 移植仕様書

## 1. Objective（目的）
ADX（平均方向性指数）を 7 種の適用価格で算出し、各値を「系列平均からの標準化距離」へ
単位変換して合算した**符号付きオシレーター（市場の「温度」）**を可視化する。トレンドの
強さ（方向性の明確さ）の偏差を、0 を基準としたヒストグラムで表現する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（ADX→レベルカウント→σ 水準→クランプ）/ 描画（separate window の
  ヒストグラム + σ 水準線）/ 入力（CSV → OHLC）。
- 対象外: ブローカー接続・チャートデータ供給、アラート、最適化入力、リアルタイム差分
  再計算（バッチ全件計算で代替。ガイド §3/§6）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fit_ADX_NEEDLE.mq4`（Copyright 2016, PRO!fit
  System Investars, version 1.00）。依存: `sample/MQL4/Include/ProfitSystem/PS.mqh`。
- 種別: MQL4。`#property indicator_separate_window`、`indicator_buffers 1` /
  `indicator_plots 1`。バッファ `ExtBufferLevelCount`（`DRAW_HISTOGRAM`,
  `indicator_color1 DarkGreen`）。σ 水準線は `indicator_levelcolor C'84,84,84'`,
  `indicator_levelstyle STYLE_SOLID`。
- input パラメータ: `int inpPeriod = 6`（ADX 期間）。
- 時系列の向き: `ArraySetAsSeries` 明示なし。`ResBufferIC01_*[i]=iADX(...,i)` で
  index 0 = 最新足（系列順）として埋める。本移植は**昇順（古い→新しい）**で扱い、
  EMA/標準偏差/平均を昇順で計算する（ガイド §4.3）。
- 使用する標準/ライブラリ関数:
  - `iADX(NULL,0,inpPeriod,PRICE_*,0,i)` … ADX 本線（MODE_MAIN）。
  - `iBandsOnArray(arr,0,length,deviation,0,{1|2},0)` … 配列全長 SMA ± deviation×母標準偏差。
  - `PS_GetLevelCountValue` / `PS_GetUnitConversion` / `PS_GetAverage` /
    `PS_GetStandardDeviationValue`（`iStdDevOnArray(...,MODE_EMA,...)`）/
    `PS_IndicatorLevelValueSet`。

## 4. Input（入力）
- 必須列: `high` / `low` / `close`（列名の大小不問）。`open` は ADX 計算に不使用
  （MetaQuotes 仕様。下記 §9）。CSV ローダは `open/high/low/close` を必須とする。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 ADX（`compute_adx`, period=6）
昇順 OHLC、各バー i（i≥1。i=0 は warm-up で 0）:
```
+DM = high[i]-high[i-1] ; if +DM<0 → 0
-DM = low[i-1]-low[i]   ; if -DM<0 → 0
if +DM==-DM:  +DM=-DM=0
elif +DM<-DM: +DM=0
else:         -DM=0
TR   = max(|high[i]-low[i]|, |high[i]-close[i-1]|, |low[i]-close[i-1]|)
+SDI = 100*(+DM)/TR (TR=0→0) ; -SDI = 100*(-DM)/TR
+DI  = EMA(+SDI, period) ; -DI = EMA(-SDI, period)   # EMA: α=2/(period+1)
DX   = 100*|+DI - -DI|/(+DI + -DI)  (分母 0→0)
ADX  = EMA(DX, period)
```
EMA は `ema[0]=x[0]`, `ema[k]=ema[k-1]+α(x[k]-ema[k-1])`（昇順）。ADX ∈ [0,100]。

### 5.2 レベルカウント（`compute_level_count`）
7 系統の適用価格について（MetaQuotes 仕様では同一 ADX）、平均 `avg`（算術平均, 丸め 5 桁）と
EMA 基準標準偏差 `std = sqrt(mean((adx - EMA(adx,length)_last)^2))` を求め、各 i:
```
band_up   = round(avg + std*3.29, 5)   # SIGMA_L6
band_down = round(avg - std*3.29, 5)
if adx[i] > avg:  res[i] += round( ((adx[i]-avg)/((band_up  -avg)/329))/100 , 5)
elif adx[i] < avg: res[i] += round( ((avg-adx[i])/((band_down-avg)/329))/100 , 5)
else:             res[i]  = 0
```
これを 7 回（1 回目のみ初期化）。両分岐とも `≒ (adx[i]-avg)/std`（**符号付き**：平均超で
正・平均未満で負）に帰着し、合算は `≒ 7×(adx-avg)/std`。

### 5.3 σ 水準（`compute_sigma_levels`, `iBandsOnArray` 相当）
レベルカウント全長の `mean` と母標準偏差 `popstd=sqrt(mean((x-mean)^2))` から、
各 σ∈{0.67,1.28,1.65,1.96,2.58,3.29} について `up_*=round(mean+σ·popstd,5)` /
`dn_*=round(mean-σ·popstd,5)`。

### 5.4 クランプ（出力ヒストグラム）
`needle = clip(level_count, dn_329, up_329)`（元 SD_2S6 ≤ x ≤ SD_1S6）。

### 5.5 丸め・補間方式
- `NormalizeDouble(_,5)` → `round(x,5)`。平均・EMA 標準偏差バンド・単位変換結果・σ 水準に適用。
- EMA は 2/(N+1)（Wilder の 1/N ではない）。標準偏差は母分散ベース（÷N）。

## 6. Entities / 成果物（出力データ）
`build_adx_needle` の DataFrame（index=入力 index）:
| 列 | 意味 |
|---|---|
| `adx_needle` | ±3.29σ クランプ後のヒストグラム値（描画対象）。符号付き。 |
| `adx_level_count` | クランプ前レベルカウント（≒ 7×(adx-平均)/std）。 |
| `adx` | 単一 ADX 本線（7 系統共通, [0,100]）。 |

σ 水準線は `needle_levels`（`up_067..up_329` / `dn_067..dn_329` + `upper_clamp` /
`lower_clamp`）でスカラ提供（時系列ではないため成果物 DataFrame と分離）。
EMPTY_VALUE 相当の非描画点は本指標では発生しない（全バー算出）。

## 7. Output（描画）
- 別ウィンドウ（separate window）型。matplotlib は下段ペインに棒ヒストグラム（DarkGreen,
  0 基準線あり）+ σ 上方水準線（点線, C'84,84,84'）。
- lightweight-charts は `create_histogram`（名前 `adx_needle`, DarkGreen, price_line/
  label=False）+ σ 上方水準線を `horizontal_line`（点線）。fill は不使用（ガイド §6）。

## 8. Exception（異常系）
- HLC 列欠落: `KeyError`（`build_adx_needle` / loader / lwc_chart）。
- HLC 長不一致・空: `ValueError`（`compute_adx`）。
- `period<=0`: `ValueError`。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（lwc_chart）。

## 9. 元 MQL からの差分
### 一致を保証する点
- ADX 算式（+DM/-DM の非対称ゼロ化、TR、+SDI/-SDI、EMA 平滑、DX、ADX）を MetaQuotes 版
  MT4 `iADX` に一致させる。EMA=2/(N+1)、母標準偏差。
- レベルカウントの符号（平均超=正/未満=負）、単位変換式、7 回加算、`NormalizeDouble(_,5)`
  の適用箇所。σ バンド（SMA±σ·母std）とクランプ（±3.29σ）。

### 意図的に変えた / 前提化した点（根拠）
1. **applied_price の vestigial 化（最重要）**: MetaQuotes 版 MT4 `iADX` は方向性移動を
   High/Low、True Range を High/Low/Close から算出し、`applied_price` は計算式に実質的に
   入らない（MQL5 で同パラメータが削除された理由＝「価格基準が変わっても方向性は同じ」）。
   よって元の 7 種呼び出しは同一 ADX を返し、レベルカウントは 7×。本移植はこの仕様を採用し、
   `APPLIED_PRICES` は加算回数（7）の保持にのみ用いる。
   - 出典: [iADX MQL4 Reference](https://docs.mql4.com/indicators/iadx),
     [iADX MQL4↔MQL5 forum](https://www.mql5.com/en/forum/161546),
     [MT4 ADX(14) 式の逆解析](https://www.prorealcode.com/topic/conversion-of-indicator-adx14-from-the-metatrader4-trading-software/)。
2. **ビット完全一致は非保証**: MT4 実機の参照値（各 applied_price の iADX 出力 / 最終
   ヒストグラム CSV）が無いため、EMA の初期化（先頭バー seed）や warm-up 区間は実機と
   厳密一致しない可能性がある。完全一致が必要な場合は参照 CSV による回帰固定が必要。
3. **`int` 切り捨ては持ち込まない**（ガイド §4.1）。元に整数化は無く、float 精度で実装。
