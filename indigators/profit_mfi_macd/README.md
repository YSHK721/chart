# PRO!fitMFIMACD（Python 移植）

出来高加重の資金流入出（Money Flow Index, MFI, 既定 `MFIperiod=13`）を起点に、その
`FastEMA=4` / `SlowEMA=8` の EMA 差分から **MACD**（`Macd=Fast-Slow`）と **Signal**
（`EMA(Macd, SignalEMA=4)`）を求め、ヒストグラム `Histogram=2.618×(Macd-Signal)` を
別ウィンドウ（**MACD 型・[0,100] 制約なし**）に描く MQL4 インジケーターの Python 移植。
ヒストグラム全系列の `平均 ±1/2/3σ`（p1/p2/p3, m1/m2/m3）と中央線 `50`（mid50）を
水準線として引く。元 `sample/MQL4/Indicators/PRO!fitMFIMACD.mq4`（Copyright (c) 2013,
PRO!fit Investars）準拠。移植方針は `indigators/PORTING_GUIDE.md` に従う。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `iMFI(MFIperiod)` / `iMAOnArray(MODE_EMA)`×3（Fast/Slow/Signal, 共有再利用）/ `Macd=Fast-Slow` / `Histogram=2.618×(Macd-Signal)` / `iStdDevOnArray`＋`iMAOnArray(MODE_SMA)`（σ 7 水準） |
| `src/mfimacd.py` | 成果物層（DataFrame 整形） | `MacdHistogramBuffer` / `MacdBuffer` / `SignalBuffer` 書き込み / StDevA1..A6 ＋ 50 水準 |
| `src/loader.py` | 入力アダプタ（CSV → OHLCV） | `OnCalculate` 引数 high/low/close/**volume**[] の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window（MACD 型・[0,100] 制約なし）+ DRAW_HISTOGRAM ＋ DRAW_LINE×2 + σ 水準線 7 本 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_histogram`×1 / `create_line`×2 / `horizontal_line`×7） |
| `tests/test_core.py` | core 計算の検証 | — |
| `tests/test_mfimacd.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake） | — |
| `demo.py` | デモ（合成 OHLCV → PNG） | — |

> `src/plot.py` は matplotlib 依存のため、先例（profit_mfi）同様 `src/__init__.py` の
> 公開 API から除外する（matplotlib 未導入環境でも `import src` を壊さないため）。
> PNG 描画は `from src.plot import plot_mfimacd` で明示的に import する。

## 使い方

```python
from src import load_ohlcv_csv, build_mfimacd, mfimacd_levels

df = load_ohlcv_csv("ohlcv.csv")            # open/high/low/close/volume 必須（大小不問）
out = build_mfimacd(df, mfi_period=13, fast=4, slow=8, signal=4)
#   -> mfimacd_hist / mfimacd_macd / mfimacd_signal の 3 列（warm-up は 0 起点）
levels = mfimacd_levels(df, mfi_period=13, fast=4, slow=8, signal=4)
#   -> p1/p2/p3/m1/m2/m3/mid50(=50)
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_mfimacd
plot_mfimacd(df, "out.png", mfi_period=13, fast=4, slow=8, signal=4)
#   下段にヒストグラム＋MFIMACD/Signal 線＋σ 水準線 7 本（自動スケール）
```

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_histogram` / `create_line` /
`horizontal_line` を持つオブジェクトを duck typing で受ける。`lightweight_charts` は
import しない）:
```python
from src.lwc_chart import add_mfimacd
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_mfimacd(sub, df, mfi_period=13, fast=4, slow=8, signal=4)
#   ヒストグラム1本(mfimacd_hist)＋線2本(MFIMACD/Signal)＋水準線7本
```

### デモ / テスト
```bash
python demo.py                 # profit_mfi_macd_demo.png を生成
python -m pytest -q            # 全テスト
```

## 計算の要点

1. **iMFI**: `TP=(H+L+C)/3`, `MF=TP×Volume`。直近 `mfi_period` 本の窓で
   `TP[j]>TP[j-1]→正MF` / `TP[j]<TP[j-1]→負MF`（**同値は非加算・非対称**）。
   `MFI=100×正MF/(正MF+負MF)`。ゼロ割は 負MF0→100 / 正MF0→0 / 両0→100。warm-up は 0。
2. **Fast/Slow/Signal EMA**: MFI / Macd 系列（warm-up 0 込み）を**共有 `moving_averages`
   の EMA** で平滑化（in-package 再実装しない）。
3. **Macd / Signal / Histogram**: `Macd=Fast-Slow`、`Signal=EMA(Macd,SignalEMA)`、
   `Histogram=2.618×(Macd-Signal)`（**係数 2.618 を厳密保持**）。
4. **σ 7 水準**: **Histogram（係数適用後）全系列**（warm-up の 0 込み）の
   `平均 ±{1,2,3}×母標準偏差（÷N）` ＋ 中央線 50。`iStdDevOnArray` /
   `iMAOnArray(MODE_SMA)` 相当。
5. **描画**: MACD 型・別ウィンドウ。元指標は `indicator_minimum/maximum` 未指定のため
   **[0,100] 制約なし（自動スケール）**。

## 元 MQL からの主な差分

- **warm-up は 0**: `i<mfi_period` の区間は元 iMFI 既定どおり 0 起点（NaN ではない）。
- **iMFI の 負MF==0→100 を組込準拠**で再現（正MF0→0 / 両0→100）。
- **同値非対称**: `TP[j]==TP[j-1]` は正負いずれにも加算しない（元挙動の 1:1 再現）。
- **2.618 係数を厳密保持**（`Histogram=2.618×(Macd-Signal)`）。
- **統計に warm-up の 0 が混入する**: σ 7 水準（平均・母標準偏差）は **histogram
  (係数適用後)** の warm-up 0 を除外せず全系列で算出する。いずれも**原挙動の 1:1 再現**で
  あり "改善" しない（SPEC §9）。
- **EMA は共有 moving_averages を流用**（重複排除）。**iMFI・σ 統計は in-package**
  （バッチ後 common 集約予定）。
- **volume の tick/実出来高 は CSV 列定義依存で bit-exact 非保証**。MT4 純正との厳密一致は
  参照 CSV が無いため保証しない。

詳細仕様は `SPEC.md` を参照。
