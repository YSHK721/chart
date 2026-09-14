# PRO!fitRMMMACD 移植仕様書

## 1. Objective（目的）
RMM レベルカウント（iRSI / iWPR / iMFI / MAROD の 4 オシレーターを `funLevelCount`
で採点・合算した `level_count`）を起点に、その `FastEMA=4` / `SlowEMA=8` の EMA から
MACD（**`Macd=Slow-Fast`**）と Signal（`EMA(Macd, SignalEMA=4)`）を求め、ヒストグラム
**`Histogram=Macd-Signal`（係数なし）**を別ウィンドウ（**MACD 型・[0,100] 制約なし**）
に描く。**σ 水準線は持たない**（元 `funIndicatorSet` を OnCalculate で呼ばず水準を
出力しない）。`inpOscillatorPeriod=6` / `inpMovingAveragePeriod=6` を既定とする。

## 2. Scope（範囲・対象外）
- 移植する: 計算（level_count(RMM) → Fast/SlowEMA → Macd(=Slow-Fast) → Signal →
  Histogram(=Macd-Signal・係数なし)）/ 描画（separate window・MACD 型のヒストグラム
  1 本＋線 2 本・**水準線なし**）/ 入力（CSV → OHLCV）。
- 対象外: ブローカー接続・チャートデータ供給、アラート、最適化入力、リアルタイム差分
  再計算（バッチ全件計算で代替。ガイド §3/§6）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fitRMMMACD.mq4`（内部タイトル "Custom
  RSIMACD.mq4" 系列, Copyright (c) PRO!fit Investars）。MQL4。
- 種別: `#property indicator_separate_window`。**`indicator_minimum`/
  `indicator_maximum` の指定なし（[0,100] 制約なし・自動スケール）**。
- バッファ/プロット（描画 3 本）:
  - プロット 0 `MacdHistogramBuffer`: `SetIndexStyle(0, DRAW_HISTOGRAM)`,
    `indicator_color1 C'133,219,24'`, ラベル `"MacdHistogram"`。
  - プロット 1 `MacdBuffer`: `SetIndexStyle(1, DRAW_LINE)`,
    `indicator_color2 C'205,232,65'`, ラベル **`"RMMWMACD"`**。
  - プロット 2 `SignalBuffer`: `SetIndexStyle(2, DRAW_LINE)`,
    `indicator_color3 C'167,197,32'`, ラベル `"Signal"`。
  - 計算用バッファ（非描画）: `FastEmaBuffer` / `SlowEmaBuffer` / `ExtBufferLevelCount`。
  - 短名 `"RMM(" + inpOscillatorPeriod + ") MACD(" + FastEMA + "," + SlowEMA + "," + SignalEMA + ")"`。
- **σ 水準線: なし**。`funIndicatorSet`（`IndicatorSetInteger(INDICATOR_LEVELS,...)`
  ＋ `IndicatorSetDouble(INDICATOR_LEVELVALUE,...)`）は **定義のみで OnCalculate から
  呼ばれない**。したがって水準線は一切出力されない（MFIMACD/RSIMACD との差分）。
- input パラメータ（input）:
  - `int inpOscillatorPeriod = 6`（オシレーター期間）。
  - `int inpMovingAveragePeriod = 6`（MAROD 用 MA 期間）。
  - `int FastEMA = 4`（FastEMA 期間）。
  - `int SlowEMA = 8`（SlowEMA 期間）。
  - `int SignalEMA = 4`（SignalEMA 期間）。
- 時系列の向き: `iMAOnArray(...,i)` 等は系列順で埋める。本移植は**昇順（古→新）**で
  計算する（ガイド §4.3）。窓内集計・全系列統計のため向きによる差は出ない。
- 使用する標準/ライブラリ関数:
  - `funLevelCount(OSI, StdDevX3, Deo)` … 4 ケース採点（profit_rmm と同一）。
  - level_count = iRSI/iWPR/iMFI/MAROD の funLevelCount 合算（profit_rmm と同一）。
  - `iMAOnArray(ExtBufferLevelCount, limit, FastEMA/SlowEMA, 0, MODE_EMA, i)` …
    level_count 系列の EMA 平滑（Fast/Slow）。
  - `MacdBuffer[i] = SlowEmaBuffer[i] - FastEmaBuffer[i]`（**L272・Slow - Fast**）。
  - `iMAOnArray(ExtBufferLevelCount→Macd, ..., SignalEMA, MODE_EMA, i)` … Macd の EMA。
  - `MacdHistogramBuffer[i] = (MacdBuffer[i] - SignalBuffer[i])`（**L280・係数なし**）。

## 4. Input（入力）
- 必須列: `high` / `low` / `close` / **`volume`**（列名の大小不問）。CSV ローダ
  `load_ohlcv_csv` は `open/high/low/close/volume` を必須とする（`open` は RMMMACD
  計算には不使用だが OHLCV 整合のため必須化）。
- **volume は必須**。level_count 算出に含まれる iMFI が出来高を使うため。本移植は
  入力 CSV の `volume` 列をそのまま採用する（tick 出来高 / 実出来高 の別は CSV の列
  定義に依存し、本移植は区別しない）。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 level_count（`compute_rmm_level_count`, osc_period=6, ma_period=6）
profit_rmm の level_count 算出パイプライン全体を **verbatim 複製**する。同一入力で
`profit_rmm.compute_rmm(...).level_count` と bit-for-bit 一致する:
```
typical = (high+low+close)/3
rsi   = iRSI(typical, osc_period)             # Wilder 平滑・flat→50・warm-up 0
mfi   = iMFI(high,low,close,volume, osc_period)  # 負MF==0→100・同値非加算非対称・warm-up 0
wpr   = iWPR(high,low,close, osc_period) + 100   # 権威 WPR.mq5・maxH==minL→前値
ma    = EMA(typical, ma_period)               # 共有 moving_averages
marod = (typical - ma)/ma*100
各オシレーターの span = avg±3σ のスパン（RSI/WPR/MFI はクランプ、MAROD はクランプ無し）
各バー i:
  level_count[i] = Σ funLevelCount(osi, span, case)
    rsi<50→case1 / rsi>50→case0 / wpr<50→case1 / wpr>50→case0 /
    mfi<50→case1 / mfi>50→case0 / marod<0→case2 / marod>0→case3
funLevelCount の 4 ケース（ゼロ割ガード無し・退化入力は inf/nan を許容）:
  case0: r=(span-50)/200; ((osi-50)/r)/100
  case1: r=(span-50)/200; -((50-osi)/r)/100
  case2: r=(span/2)/200;  ((osi-r)/r)/100
  case3: r=(span/2)/200;  -((r-osi)/r)/100
```

### 5.2 Fast / Slow EMA（`fast=4`, `slow=8`, 共有 moving_averages）
level_count 系列（warm-up 込み）を共有 `moving_averages.exponential_ma_on_buffer` で
EMA(FastEMA) / EMA(SlowEMA) 化する。**EMA は in-package 再実装せず共有層を流用**する。
warm-up を除外せず通す（元 `iMAOnArray(MODE_EMA)` と同じ）。

### 5.3 Macd（**重要差分①: Slow - Fast**）
```
Macd[i] = Slow[i] - Fast[i]     （元 L272 MacdBuffer[i]=SlowEmaBuffer[i]-FastEmaBuffer[i]）
```
**MFIMACD/RSIMACD の `Fast-Slow` とは符号が逆**。

### 5.4 Signal
```
Signal = EMA(Macd, SignalEMA=4)   （共有 moving_averages, warm-up 込み）
```

### 5.5 Histogram（**重要差分②: 係数なし**）
```
Histogram[i] = Macd[i] - Signal[i]   （元 L280, 係数 2.618 を掛けない）
```
**MFIMACD/RSIMACD の `2.618×(Macd-Signal)` とは係数の有無が異なる**（RMMMACD のみ係数
なし）。

### 5.6 σ 水準（**なし**）
本指標は σ 水準線を出力しない。`funIndicatorSet` は定義のみで OnCalculate から呼ばれ
ないため、`compute_rmmmacd` は σ levels を算出せず、`RmmMacdResult` に levels
フィールドを持たない。`build_rmmmacd` は levels 関数（`rmmmacd_levels`）を持たない。

### 5.7 計算順序（元 MQL の 1:1 再現）
```
1. level_count = compute_rmm_level_count(high,low,close,volume,osc_period,ma_period)
2. fast = EMA(level_count, FastEMA) ; slow = EMA(level_count, SlowEMA)
3. macd = slow - fast                 # 重要差分①（L272）
4. signal = EMA(macd, SignalEMA)
5. hist = macd - signal               # 重要差分②（L280・係数なし）
```

### 5.8 丸め・補間方式
- 元コードに `NormalizeDouble` は無く、float 精度で実装（ガイド §4.1, int 切り捨ても
  持ち込まない）。
- funLevelCount のゼロ割はガードしない（退化入力は inf/nan を許容・1:1 再現）。

## 6. Entities / 成果物（出力データ）
`build_rmmmacd` の DataFrame（index=入力 index, 描画対象 3 列のみ）:
| 列 | 意味 |
|---|---|
| `rmmmacd_hist`（`HIST_COLUMN`） | `Macd-Signal`（係数なし。描画対象, ヒストグラム）。 |
| `rmmmacd_macd`（`MACD_COLUMN`） | `Slow-Fast`（描画対象, 線 "RMMWMACD"）。 |
| `rmmmacd_signal`（`SIGNAL_COLUMN`） | `EMA(Macd, SignalEMA)`（描画対象, 線 "Signal"）。 |

中間 `level_count`/`fast`/`slow` は描画不要のため列化しない（`compute_rmmmacd` の DTO
では検証用に保持）。**σ 水準は存在しないため levels 関数・levels 列を持たない**。

## 7. Output（描画）
- 別ウィンドウ（separate window）型・**MACD 型**。元指標は `indicator_minimum`/
  `indicator_maximum` を指定しないため、**y 範囲は自動スケール（[0,100] 制約なし）**。
- matplotlib（`plot.py`）: 下段ペインに **ヒストグラム**（bar, C'133,219,24'）＋
  **RMMWMACD 線**（C'205,232,65'）＋ **Signal 線**（C'167,197,32'）。**σ 水準線は
  描かない**。自動スケール。
- lightweight-charts（`lwc_chart.py`）: `create_histogram` 1 本（name=`rmmmacd_hist`）
  ＋ `create_line` 2 本（name=`RMMWMACD` / `Signal`, style solid,
  price_line/label=False）。**`horizontal_line` は呼ばない（水準線なし）**。系列名は
  値列名と完全一致（ガイド §5）— ヒストグラムは `rmmmacd_hist`、線は値列
  `rmmmacd_macd`/`rmmmacd_signal` をライン名 `RMMWMACD`/`Signal` の列名にマップして
  set する。`lightweight_charts` は import せず duck typing で受ける。

## 8. Exception（異常系）
- HLC / volume 列欠落: `KeyError`（`build_rmmmacd` / `lwc_chart`）。
- CSV 必須列（open/high/low/close/volume）欠落・時刻列欠落: `KeyError`
  （`load_ohlcv_csv`）。
- CSV ファイル不在: `FileNotFoundError`（`load_ohlcv_csv`）。
- OHLCV 長不一致: `ValueError`（`compute_rmm_level_count`）。
- `osc_period < 2`: `ValueError`（`compute_rmm_level_count`）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（`lwc_chart`）。

## 9. 元 MQL からの差分

### 一致を保証する点（原挙動の 1:1 再現）
- **level_count は profit_rmm の verbatim 複製**（iRSI / iWPR / iMFI / MAROD・
  funLevelCount 4 ケース採点・合算・クランプ非対称・warm-up・iWPR 権威・flat→50・
  負MF==0→100 を完全保持）。同一入力で `profit_rmm.compute_rmm(...).level_count` と
  bit-for-bit 一致する。
- **重要差分①: `Macd=Slow-Fast`**（元 L272）。MFIMACD/RSIMACD の `Fast-Slow` とは
  符号が逆。これを 1:1 再現する。
- **重要差分②: `Histogram=Macd-Signal`（係数なし）**（元 L280）。MFIMACD/RSIMACD の
  `2.618×(Macd-Signal)` のような係数を掛けない（**RMMMACD のみ係数なし**）。これを
  1:1 再現する。
- **重要差分③: σ 水準線なし**。元 `funIndicatorSet` は OnCalculate から呼ばれず、
  水準（INDICATOR_LEVELS）を出力しない。本移植も σ levels を一切持たない（DTO
  フィールドなし・levels 関数なし・horizontal_line 呼出なし）。
- Fast/Slow/Signal の EMA は共有 `moving_averages`（`exponential_ma_on_buffer`）で
  算出し、warm-up を除外せず通す（元 `iMAOnArray(MODE_EMA)` 相当）。
- funLevelCount のゼロ割は 1:1 再現（退化入力で inf/nan を許容・ガードしない）。
- MACD 型・[0,100] 制約なし（元指標は indicator_minimum/maximum を指定しない）。

### 中立記載（原挙動の 1:1 再現として保持し "改善" しない）
- warm-up の扱い・負MF==0→100・同値非対称・funLevelCount のゼロ割非ガード・係数なし
  Histogram・Slow-Fast の符号は、いずれも元 MQL の挙動そのものである。除外・補正・
  係数付与・符号反転は行わず**原挙動として 1:1 再現する**（良し悪しの判断を持ち込ま
  ない中立記載）。

### 意図的に変えた / 前提化した点（根拠）
1. **EMA（Fast/Slow/Signal）は共有 moving_averages を流用**: 元 `iMAOnArray(MODE_EMA)`
   相当の `exponential_ma_on_buffer` を共有層から再利用し、in-package 再実装しない
   （重複排除）。
2. **level_count パイプライン（iRSI/iWPR/iMFI/MAROD・採点）は profit_rmm の verbatim
   複製を in-package に閉じる**: 同一性維持のため共通化しない。**バッチ後に common へ
   集約予定**（現時点は YAGNI で in-package。共有需要が顕在化してから昇格）。
3. **`int` 切り捨て・`NormalizeDouble` は持ち込まない**（ガイド §4.1）。元コードに整数化・
   丸めは無く、float 精度で実装する。
4. **volume の tick / 実出来高 は CSV 列定義依存で bit-exact 非保証**: 元 `iMFI` は MT4
   チャートの既定出来高を参照するが、本移植は入力 CSV の `volume` 列をそのまま採用する。
   MT4 実機との bit-exact 一致は保証しない。完全一致が必要な場合は MT4 出力 CSV による
   回帰固定が必要。

## 10. テスト
- `tests/test_core.py`: level_count（profit_rmm 完全一致）/ macd=slow-fast（符号）/
  histogram=macd-signal（係数なし）/ EMA 連鎖 / σ 水準の不在（DTO 構造）/ 例外 /
  DTO 不変性 の core 計算。
- `tests/test_rmmmacd.py`: 成果物層（3 列付与・元 index 継承・列名大小不問・volume 欠落
  例外・levels 関数の不在）。
- `tests/test_lwc_chart.py`: Fake チャートで ヒスト 1 本・線 2 本・**水平線 0 本**・
  name↔値列一致・値一致・異常系（必須列欠落・volume 欠落・時刻欠落）を検証。
