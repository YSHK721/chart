# PRO!fitMFI 移植仕様書

## 1. Objective（目的）
出来高加重の資金流入出（Money Flow Index, MFI, 既定 period=14）を別ウィンドウ
（[0,100]）の MFI 線と、その EMA 平滑線（既定 ma_period=5）で可視化し、平滑系列全体の
`平均 ±1/2/3σ`（p1/p2/p3, m1/m2/m3）と中央線 50（mid50）を統計的水準線として描く。
価格と出来高の組（TP×Volume）の正負バランスから買い圧力・売り圧力の相対強度を表現する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（iMFI → EMA 平滑 → σ 7 水準）/ 描画（separate window [0,100] の
  MFI 線・EMA 線 ＋ σ 水準線 7 本）/ 入力（CSV → OHLCV）。
- 対象外: ブローカー接続・チャートデータ供給、アラート、最適化入力、リアルタイム差分
  再計算（バッチ全件計算で代替。ガイド §3/§6）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fitMFI.mq4`（Copyright 2015, PRO!fit
  Investars, description "Money Flow Index"）。MQL4。
- 種別: `#property indicator_separate_window`、`indicator_buffers 2`、
  `indicator_minimum 0` / `indicator_maximum 100`。
- バッファ/プロット: `ExtMFIBuffer`（プロット 0, `SetIndexStyle(0, DRAW_LINE)`,
  `indicator_color1 clrLime`）/ `ExtMABuffer`（プロット 1, `SetIndexStyle(1,
  DRAW_LINE)`, `indicator_color2 clrLime`）。短名 `"MFI (" + InpMFIPeriod + ")"`。
- σ 水準線: `indicator_levelcolor C'84,84,84'`（グレー）, `indicator_levelstyle
  STYLE_SOLID`。`IndicatorSetInteger(INDICATOR_LEVELS, 5)`（後続 SetDouble は index
  0..6 まで設定するため実質 ±1/2/3σ ＋ 中央 50 の 7 水準）。
- input パラメータ:
  - `int InpMFIPeriod = 14`（MFI 期間）。**`InpMFIPeriod < 2` で `return(INIT_FAILED)`**。
  - `int InpMAPeriod = 5`（EMA 平滑期間）。
- 時系列の向き: `ArraySetAsSeries` 明示なし。`iMFI(...,i)` / `iMAOnArray(...,i)` は
  index 0 = 最新足（系列順）として埋める。本移植は**昇順（古→新）**で計算する
  （ガイド §4.3）。窓内集計・全系列統計のため向きによる差は出ない。
- 使用する標準/ライブラリ関数:
  - `iMFI(NULL, 0, InpMFIPeriod, i)` … TP=(H+L+C)/3, MF=TP×Volume の正負集計から
    MFI=100×正MF/(正MF+負MF)。
  - `iMAOnArray(ExtMFIBuffer, 0, InpMAPeriod, 0, MODE_EMA, i)` … MFI 系列の EMA 平滑。
  - `iStdDevOnArray(ExtMABuffer, 0, rates_total, 0, MODE_SMA, 0)` … 平滑系列全長の
    母標準偏差（÷N）。
  - `iMAOnArray(ExtMABuffer, 0, rates_total, 0, MODE_SMA, 0)` … 平滑系列全長の算術平均。
  - `StDevA1..A6 = avg ± StDev×{1,2,3}`、`INDICATOR_LEVELVALUE index 4 = 50`。

## 4. Input（入力）
- 必須列: `high` / `low` / `close` / **`volume`**（列名の大小不問）。CSV ローダ
  `load_ohlcv_csv` は `open/high/low/close/volume` を必須とする（`open` は MFI 計算
  には不使用だが OHLCV 整合のため必須化）。
- **volume は必須**。元 MQL4 の `iMFI` は MT4 チャートの既定出来高を用いる。本移植は
  入力 CSV の `volume` 列をそのまま採用する（tick 出来高 / 実出来高 の別は CSV の列
  定義に依存し、本移植は区別しない）。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 iMFI（`compute_mfi`, period=14）
昇順 OHLCV、各バー `i`（`i >= period`。`i < period` は warm-up）:
```
TP[i] = (high[i] + low[i] + close[i]) / 3
MF[i] = TP[i] * volume[i]
```
窓 `[i-period+1 .. i]` の各 `j`（`j >= 1`）で正負分類（同値は非加算・非対称）:
```
TP[j] > TP[j-1]  -> 正MF += MF[j]
TP[j] < TP[j-1]  -> 負MF += MF[j]
TP[j] == TP[j-1] -> 加算しない（非対称・同値非加算）
MFI[i] = 100 * 正MF / (正MF + 負MF)
```
ゼロ割の場合分け（一意・組込 iMFI 準拠 = MetaQuotes 公式 MFI.mq5 L107-110 / MFI.mq4 L86-89）:
```
負MF != 0              -> MFI[i] = 100 - 100/(1 + 正MF/負MF) = 100*正MF/(正MF+負MF)
負MF == 0              -> MFI[i] = 100   （all-up / flat window で 正MF==0 も含め一律 100）
正MF == 0 かつ 負MF>0  -> MFI[i] = 0     （上式の帰結）
i < period             -> MFI[i] = 0   （warm-up は 0。NaN ではない）
```

### 5.2 EMA 平滑（`ma_period=5`, 共有 moving_averages）
MFI 系列（warm-up 0 込み）を共有 `moving_averages.exponential_ma_on_buffer` で
EMA(ma_period) 化する。**EMA は in-package 再実装せず共有層を流用**する。warm-up 0 を
除外せず通す（元 `iMAOnArray(MODE_EMA)` と同じ）。`ma_period<=1` は共有関数の挙動に従う。

### 5.3 σ 7 水準（`compute_mfi_levels`）
EMA 平滑系列**全長**（**warm-up の 0 を除外せず**）の算術平均 `avg` と母標準偏差
`σ = sqrt(mean((x-avg)^2))`（÷N）から:
```
p1 = avg + 1σ    p2 = avg + 2σ    p3 = avg + 3σ
m1 = avg - 1σ    m2 = avg - 2σ    m3 = avg - 3σ
mid50 = 50.0     （元 INDICATOR_LEVELVALUE index 4 = 50, 固定中央線）
```
元 `iStdDevOnArray` / `iMAOnArray`（MODE_SMA, period=rates_total）に対応。

### 5.4 丸め・補間方式
- 元コードに `NormalizeDouble` は無く、float 精度で実装（ガイド §4.1, int 切り捨ても
  持ち込まない）。
- 標準偏差は母分散ベース（÷N, MT4 iStdDev 準拠）。

## 6. Entities / 成果物（出力データ）
`build_mfi` の DataFrame（index=入力 index）:
| 列 | 意味 |
|---|---|
| `mfi`（`MFI_COLUMN`） | iMFI 値（描画対象, 線）。warm-up は 0。 |
| `mfi_ma`（`MA_COLUMN`） | iMFI の EMA 平滑値（描画対象, 線）。 |

σ 7 水準は `mfi_levels`（`p1/p2/p3/m1/m2/m3/mid50`）でスカラ提供（時系列ではなく価格軸の
水平参照値のため成果物 DataFrame と分離）。EMPTY_VALUE 相当の非描画点は本指標では発生
しない（warm-up も 0 で全バー算出）。

## 7. Output（描画）
- 別ウィンドウ（separate window）型。y 範囲 [0,100]（元 indicator_minimum 0 /
  indicator_maximum 100）。
- matplotlib: 下段ペインに **MFI 線**（Lime）＋ **EMA 平滑線** ＋ σ 水準線 7 本
  （±1/2/3σ は点線グレー C'84,84,84'、中央線 50 は実線）。
- lightweight-charts: `create_line` 2 本（name=`mfi` / `mfi_ma`, clrLime, style solid,
  price_line/label=False）＋ σ 水準線 7 本を `horizontal_line`（SOLID, グレー）。多数線の
  ため price_line/label=False（ガイド §6）。ライン名は値列名（`mfi`/`mfi_ma`）と完全
  一致（ガイド §5）。`lightweight_charts` は import せず duck typing で受ける。

## 8. Exception（異常系）
- HLC / volume 列欠落: `KeyError`（`build_mfi` / `mfi_levels` / `lwc_chart`）。
- CSV 必須列（open/high/low/close/volume）欠落・時刻列欠落: `KeyError`（`load_ohlcv_csv`）。
- CSV ファイル不在: `FileNotFoundError`（`load_ohlcv_csv`）。
- OHLCV 長不一致: `ValueError`（`compute_mfi`）。
- `mfi_period < 2`: `ValueError`（`compute_mfi`。元 OnInit の `INIT_FAILED` に対応）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（`lwc_chart`）。

## 9. 元 MQL からの差分

### 一致を保証する点（原挙動の 1:1 再現）
- iMFI 算式（TP=(H+L+C)/3, MF=TP×Volume, 窓内正負集計, MFI=100×正MF/(正MF+負MF)）。
- **同値非対称（TP[j]==TP[j-1] は正負いずれにも加算しない）**を 1:1 再現する。
- **warm-up（`i<period`）は 0**（元 iMFI / SetIndexDrawBegin 既定。NaN ではない）。
- **ゼロ割の場合分け**（組込 iMFI=MetaQuotes 公式準拠）: 負MF==0→100（all-up / flat window
  で 正MF==0 も含め一律 100） / 負MF>0 かつ 正MF==0→0 を 1:1 再現する。
- σ 7 水準（p1/p2/p3/m1/m2/m3 ＝ 全系列 `平均 ±{1,2,3}×母標準偏差`、mid50=50）。
  **統計に warm-up の 0 が混入する**点も元挙動どおり除外せず再現する。
- subwindow 範囲 [0,100]（元 indicator_minimum 0 / indicator_maximum 100）。
- `mfi_period < 2` で例外（元 OnInit の INIT_FAILED）。

### 中立記載（原挙動の 1:1 再現として保持し "改善" しない）
- **warm-up 0・負MF==0→100・σ 統計への 0 混入・同値非対称**は、統計的には平均・σ を歪める
  効果があるが、これは元 MQL の挙動そのものである。除外・補正・対称化は行わず**原挙動
  として 1:1 再現する**（良し悪しの判断を持ち込まない中立記載）。

### 意図的に変えた / 前提化した点（根拠）
1. **EMA 平滑は共有 moving_averages を流用**: 元 `iMAOnArray(MODE_EMA)` 相当の
   `exponential_ma_on_buffer` を共有層から再利用し、in-package 再実装しない（重複排除）。
2. **iMFI は共有 `mql_builtins` へ集約済み・σ 統計のみ in-package**: `iMFI` は共有
   `mql_builtins.compute_mfi` を import 再公開して in-package 参照面を維持する
   （MFIMACD 着手に伴い共有昇格を実施済み）。`σ 統計` のみ本指標専用プリミティブ
   として `src/core.py` 内に閉じる。
3. **`int` 切り捨て・`NormalizeDouble` は持ち込まない**（ガイド §4.1）。元コードに整数化・
   丸めは無く、float 精度で実装。
4. **volume の tick / 実出来高 は CSV 列定義依存で bit-exact 非保証**: 元 `iMFI` は MT4
   チャートの既定出来高を参照するが、本移植は入力 CSV の `volume` 列をそのまま採用する。
   tick 出来高か実出来高かは CSV 列定義に依存し、MT4 実機との bit-exact 一致は保証しない。
   完全一致が必要な場合は MT4 出力 CSV による回帰固定が必要。
