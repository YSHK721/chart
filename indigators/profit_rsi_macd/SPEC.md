# PRO!fitRSIMACD 移植仕様書

## 1. Objective（目的）
価格 Typical（`PRICE_TYPICAL=(H+L+C)/3`）を起点とする相対力指数（RSI, 既定
`RSIperiod=13`）を求め、その `FastEMA=4` / `SlowEMA=8` の EMA 差分から MACD
（`Macd=Fast-Slow`）と Signal（`EMA(Macd, SignalEMA=4)`）を求め、ヒストグラム
`Histogram=2.618×(Macd-Signal)` を別ウィンドウ（**MACD 型・[0,100] 制約なし**）に描く。
ヒストグラム全系列の `平均 ±1/2/3σ`（p1/p2/p3, m1/m2/m3）と中央線 `50`（mid50）を統計的
水準線として引く。RSI のモメンタムを織り込んだ MACD として収束/発散を表現する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（iRSI(13) → Fast/SlowEMA → Macd → Signal → Histogram(2.618 係数)
  → σ 7 水準）/ 描画（separate window・MACD 型のヒストグラム 1 本＋線 2 本＋σ 水準線
  7 本）/ 入力（CSV → OHLC）。
- 対象外: ブローカー接続・チャートデータ供給、アラート、最適化入力、リアルタイム差分
  再計算（バッチ全件計算で代替。ガイド §3/§6）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fitRSIMACD.mq4`（内部タイトル "Custom
  RSIMACD.mq4", Copyright (c) 2013, PRO!fit Investars）。MQL4。
- 種別: `#property indicator_separate_window`、`#property indicator_buffers 3`
  （`init()` で `IndicatorBuffers(6)`）。**`indicator_minimum`/`indicator_maximum`
  の指定なし（[0,100] 制約なし・自動スケール）**。
- バッファ/プロット（描画 3 本）:
  - プロット 0 `MacdHistogramBuffer`: `SetIndexStyle(0, DRAW_HISTOGRAM)`,
    `indicator_color1 C'133,219,24'`, ラベル `"MacdHistogram"`。
  - プロット 1 `MacdBuffer`: `SetIndexStyle(1, DRAW_LINE)`,
    `indicator_color2 C'205,232,65'`, ラベル `"RSIMACD"`。
  - プロット 2 `SignalBuffer`: `SetIndexStyle(2, DRAW_LINE)`,
    `indicator_color3 C'167,197,32'`, ラベル `"Signal"`,
    `SetIndexDrawBegin(2, SignalEMA)`。
  - 計算用バッファ（非描画）: `FastEmaBuffer` / `SlowEmaBuffer` / `RsiBuffer`。
  - 短名 `"RSI(" + RSIperiod + ") MACD(" + FastEMA + "," + SlowEMA + "," + SignalEMA + ")"`。
- σ 水準線: `indicator_levelcolor C'84,84,84'`（グレー）, `indicator_levelstyle
  STYLE_SOLID`。`IndicatorSetInteger(INDICATOR_LEVELS, 5)` だが後続 `SetDouble` は
  index 0..6 を設定するため実質 ±1/2/3σ ＋ 中央 50 の 7 水準（index 0=+1σ, 1=+2σ,
  2=-1σ, 3=-2σ, 4=50, 5=+3σ, 6=-3σ）。
- input パラメータ（extern）:
  - `int RSIperiod = 13`（RSI 期間）。
  - `int FastEMA = 4`（FastEMA 期間）。
  - `int SlowEMA = 8`（SlowEMA 期間）。
  - `int SignalEMA = 4`（SignalEMA 期間）。
- 時系列の向き: `ArraySetAsSeries` 明示なし。`iRSI(...,i)` / `iMAOnArray(...,i)` は
  index 0 = 最新足（系列順）として埋める。本移植は**昇順（古→新）**で計算する
  （ガイド §4.3）。Wilder 平滑・全系列統計のため向きによる差は出ない。
- 使用する標準/ライブラリ関数:
  - `iRSI(NULL, 0, RSIperiod, PRICE_TYPICAL, i)` … 価格 Typical に対する RSI
    （Wilder 平滑）。**価格は PRICE_TYPICAL 固定**（出来高は不参照）。
  - `iMAOnArray(RsiBuffer, Bars, FastEMA/SlowEMA, 0, MODE_EMA, i)` … RSI 系列の
    EMA 平滑（Fast/Slow）。
  - `MacdBuffer[i] = FastEmaBuffer[i] - SlowEmaBuffer[i]`。
  - `iMAOnArray(MacdBuffer, Bars, SignalEMA, 0, MODE_EMA, i)` … Macd の EMA 平滑。
  - `MacdHistogramBuffer[i] = 2.618 * (MacdBuffer[i] - SignalBuffer[i])`。
  - `iStdDevOnArray(MacdHistogramBuffer, 0, rates_total, 0, MODE_SMA, 0)` … histogram
    全長の母標準偏差（÷N）。
  - `iMAOnArray(MacdHistogramBuffer, 0, rates_total, 0, MODE_SMA, 0)` … histogram
    全長の算術平均。
  - `StDevA1..A6 = avg ± StDev×{1,2,3}`、`INDICATOR_LEVELVALUE index 4 = 50`。

## 4. Input（入力）
- 必須列: `high` / `low` / `close`（列名の大小不問）。CSV ローダ `load_ohlc_csv` は
  `open/high/low/close` を必須とする（`open` は RSIMACD 計算には不使用だが OHLC 整合
  のため必須化）。
- **volume は不要**。元 MQL の `iRSI(...,PRICE_TYPICAL,...)` は価格のみを参照し出来高を
  用いない（先例 profit_mfi_macd は iMFI のため volume 必須だが、本指標は iRSI のため
  OHLC で成立する）。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 価格 Typical → iRSI（`compute_rsi`, period=13）
昇順 OHLC、各バー `i`:
```
price[i] = typical_price = (high[i] + low[i] + close[i]) / 3      # PRICE_TYPICAL 固定（共有 common 再利用）
```
RSI は権威 Wilder（MetaQuotes 公式 `RSI.mq5` 準拠）を昇順で 1:1 再現する。
`diff[i] = price[i] - price[i-1]` とし:
```
seed (i == period):
    pos = mean_{j=1..period}(max(diff[j], 0))
    neg = mean_{j=1..period}(max(-diff[j], 0))
main (i > period):
    pos[i] = (pos[i-1]*(period-1) + max(diff[i], 0)) / period
    neg[i] = (neg[i-1]*(period-1) + max(-diff[i], 0)) / period
RSI[i]:
    neg != 0            -> 100 - 100/(1 + pos/neg)
    neg == 0, pos != 0  -> 100
    neg == 0, pos == 0  -> 50          （flat window → 50）
i < period              -> 0           （warm-up は 0。NaN ではない）
```

### 5.2 Fast / Slow EMA（`fast=4`, `slow=8`, 共有 moving_averages）
RSI 系列（warm-up 0 込み）を共有 `moving_averages.exponential_ma_on_buffer` で
EMA(FastEMA) / EMA(SlowEMA) 化する。**EMA は in-package 再実装せず共有層を流用**する。
warm-up 0 を除外せず通す（元 `iMAOnArray(MODE_EMA)` と同じ）。

### 5.3 Macd / Signal
```
Macd[i]   = Fast[i] - Slow[i]
Signal    = EMA(Macd, SignalEMA=4)   （共有 moving_averages, warm-up 0 込み）
```

### 5.4 Histogram（2.618 係数）
```
Histogram[i] = 2.618 * (Macd[i] - Signal[i])
```
**係数 2.618 を厳密保持する**（元 MQL の `2.618*(MacdBuffer[i]-SignalBuffer[i])`）。

### 5.5 σ 7 水準（`compute_rsimacd_levels`）
**Histogram（=2.618 適用後）全系列**（**warm-up の 0 を除外せず**）の算術平均 `avg` と
母標準偏差 `σ = sqrt(mean((x-avg)^2))`（÷N）から:
```
p1 = avg + 1σ    p2 = avg + 2σ    p3 = avg + 3σ
m1 = avg - 1σ    m2 = avg - 2σ    m3 = avg - 3σ
mid50 = 50.0     （元 INDICATOR_LEVELVALUE index 4 = 50, 固定中央線）
```
元 `iStdDevOnArray` / `iMAOnArray`（MODE_SMA, period=rates_total, 対象=
MacdHistogramBuffer）に対応。**σ/avg は histogram（係数適用後）に掛かる**点に注意。

### 5.6 計算順序（元 MQL の 1:1 再現）
```
1. price  = typical_price(high, low, close) = (high+low+close)/3
   rsi     = compute_rsi(price, period=RSIperiod)
2. fast    = EMA(rsi, FastEMA) ; slow = EMA(rsi, SlowEMA)
3. macd    = fast - slow
4. signal  = EMA(macd, SignalEMA)
5. hist    = 2.618 * (macd - signal)
6. σ7水準  = compute_rsimacd_levels(hist)        # 係数適用後・母σ÷N・mid50=50
```

### 5.7 丸め・補間方式
- 元コードに `NormalizeDouble` は無く、float 精度で実装（ガイド §4.1, int 切り捨ても
  持ち込まない。`IndicatorDigits(Digits+1)` は表示桁のみで内部値に影響しない）。
- 標準偏差は母分散ベース（÷N, MT4 iStdDev 準拠）。

## 6. Entities / 成果物（出力データ）
`build_rsimacd` の DataFrame（index=入力 index, 描画対象 3 列のみ）:
| 列 | 意味 |
|---|---|
| `rsimacd_hist`（`HIST_COLUMN`） | `2.618×(Macd-Signal)`（描画対象, ヒストグラム）。 |
| `rsimacd_macd`（`MACD_COLUMN`） | `Fast-Slow`（描画対象, 線 "RSIMACD"）。 |
| `rsimacd_signal`（`SIGNAL_COLUMN`） | `EMA(Macd, SignalEMA)`（描画対象, 線 "Signal"）。 |

中間 `rsi`/`fast`/`slow` は描画不要のため列化しない（`compute_rsimacd` の DTO では
検証用に保持）。σ 7 水準は `rsimacd_levels`（`p1/p2/p3/m1/m2/m3/mid50`）でスカラ提供
（時系列ではなく価格軸の水平参照値のため成果物 DataFrame と分離）。warm-up も 0 起点で
全バー算出されるため EMPTY_VALUE 相当の非描画点は発生しない。

## 7. Output（描画）
- 別ウィンドウ（separate window）型・**MACD 型**。元指標は `indicator_minimum`/
  `indicator_maximum` を指定しないため、**y 範囲は自動スケール（[0,100] 制約なし）**。
- matplotlib（`plot.py`）: 下段ペインに **ヒストグラム**（bar, C'133,219,24'）＋
  **RSIMACD 線**（C'205,232,65'）＋ **Signal 線**（C'167,197,32'）＋ σ 水準線 7 本
  （±1/2/3σ は点線グレー C'84,84,84'、中央線 50 は実線）。自動スケール。
- lightweight-charts（`lwc_chart.py`）: `create_histogram` 1 本
  （name=`rsimacd_hist`）＋ `create_line` 2 本（name=`RSIMACD` / `Signal`,
  style solid, price_line/label=False）＋ σ 水準線 7 本を `horizontal_line`
  （SOLID, グレー）。多数系列のため price_line/label=False（ガイド §6）。系列名は
  値列名と完全一致（ガイド §5）— ヒストグラムは `rsimacd_hist`、線は値列
  `rsimacd_macd`/`rsimacd_signal` をライン名 `RSIMACD`/`Signal` の列名にマップして
  set する。`lightweight_charts` は import せず duck typing で受ける。

## 8. Exception（異常系）
- HLC 列欠落: `KeyError`（`build_rsimacd` / `rsimacd_levels` / `lwc_chart`）。
- CSV 必須列（open/high/low/close）欠落・時刻列欠落: `KeyError`（`load_ohlc_csv`）。
- CSV ファイル不在: `FileNotFoundError`（`load_ohlc_csv`）。
- OHLC 長不一致: `ValueError`（`compute_rsimacd`）。
- `rsi_period < 2`: `ValueError`（`compute_rsi`）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（`lwc_chart`）。

## 9. 元 MQL からの差分

### 一致を保証する点（原挙動の 1:1 再現）
- 価格 PRICE_TYPICAL 固定（`(H+L+C)/3`）→ iRSI。**価格 Typical 固定**を 1:1 再現する
  （出来高は参照しない）。
- iRSI は**権威 Wilder（MetaQuotes 公式 `RSI.mq5` 準拠）**で再現する。`neg==0→100`
  （all-up）、`neg==0 かつ pos==0 → 50`（flat window）、warm-up（i<period）→ 0
  （NaN ではない）。`compute_rsi` は共有 `mql_builtins` の再公開（是正済み）。
- Fast/Slow/Signal の EMA は共有 `moving_averages`（`exponential_ma_on_buffer`）で
  算出し、warm-up 0 を除外せず通す（元 `iMAOnArray(MODE_EMA)` 相当）。
- `Macd=Fast-Slow`、`Signal=EMA(Macd,SignalEMA)`、`Histogram=2.618×(Macd-Signal)`。
  **係数 2.618 を厳密保持**する。
- σ 7 水準（p1/p2/p3/m1/m2/m3 ＝ **histogram(係数適用後) 全系列** の
  `平均 ±{1,2,3}×母標準偏差（÷N）`、mid50=50）。**統計に warm-up の 0 が混入する**点も
  元挙動どおり除外せず再現する。
- MACD 型・[0,100] 制約なし（元指標は indicator_minimum/maximum を指定しない）。

### 中立記載（原挙動の 1:1 再現として保持し "改善" しない）
- **warm-up 0・flat→50・σ 統計への 0 混入・2.618 係数**は、統計的には平均・σ を歪める
  効果や任意性があるが、これは元 MQL の挙動そのものである。除外・補正・係数変更は行わず
  **原挙動として 1:1 再現する**（良し悪しの判断を持ち込まない中立記載）。

### 意図的に変えた / 前提化した点（根拠）
1. **iRSI は権威 Wilder（`RSI.mq5`）準拠・flat→50**: 組込 `iRSI` の Wilder 平滑と
   ゼロ割場合分けを公式 `RSI.mq5` に合わせて 1:1 再現する（`compute_rsi` は共有
   `mql_builtins` の再公開）。
2. **EMA・適用価格（Typical）は共有再利用**: 元 `iMAOnArray(MODE_EMA)` 相当の
   `exponential_ma_on_buffer`（共有 `moving_averages`）、適用価格 `typical_price`
   （共有 `common`）を流用し、in-package 再実装しない（重複排除）。
3. **iRSI は共有 `mql_builtins` へ集約済み・σ 統計のみ in-package**: `compute_rsi` は
   共有 `mql_builtins` を import 再公開して in-package 参照面を維持する。σ 統計のみ
   本指標専用プリミティブとして `src/core.py` 内に閉じる。
4. **2.618 係数の厳密保持**: 元 `Histogram=2.618*(Macd-Signal)` の係数を定数
   `_HIST_COEFFICIENT=2.618` として厳密保持する（丸め・近似・正規化しない）。
5. **`int` 切り捨て・`NormalizeDouble` は持ち込まない**（ガイド §4.1）。元コードに整数化・
   丸めは無く、float 精度で実装（`IndicatorDigits(Digits+1)` は表示桁のみ）。
6. **bit-exact 非保証**: 元 `iRSI` は MT4 内部の系列方向・浮動小数演算順で計算される。
   本移植は昇順 Wilder で 1:1 再現するが、MT4 実機との完全な bit-exact 一致は参照 CSV が
   無いため保証しない。完全一致が必要な場合は MT4 出力 CSV による回帰固定が必要。

## 10. テスト
- `tests/test_core.py`: iRSI / Fast-Slow-Macd-Signal-Histogram / σ7水準 の core 計算。
- `tests/test_rsimacd.py`: 成果物層（3 列付与・元 index 継承・列名大小不問・必須列欠落
  例外・7 水準辞書）。
- `tests/test_lwc_chart.py`: Fake チャートで ヒスト 1 本・線 2 本・水平線 7 本・name↔値列
  一致・値一致・異常系（必須列欠落・時刻欠落）を検証。
