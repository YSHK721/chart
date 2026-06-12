# PRO!fitRSIMACD（Python 移植）

価格 Typical（`PRICE_TYPICAL=(H+L+C)/3`）を起点とする相対力指数（RSI, 既定
`RSIperiod=13`）を求め、その `FastEMA=4` / `SlowEMA=8` の EMA 差分から **MACD**
（`Macd=Fast-Slow`）と **Signal**（`EMA(Macd, SignalEMA=4)`）を求め、ヒストグラム
`Histogram=2.618×(Macd-Signal)` を別ウィンドウ（**MACD 型・[0,100] 制約なし**）に描く
MQL4 インジケーターの Python 移植。ヒストグラム全系列の `平均 ±1/2/3σ`
（p1/p2/p3, m1/m2/m3）と中央線 `50`（mid50）を水準線として引く。元
`sample/MQL4/Indicators/PRO!fitRSIMACD.mq4`（Copyright (c) 2013, PRO!fit Investars）
準拠。移植方針は `indigators/PORTING_GUIDE.md` に従う。

> 本指標は `indicators/profit_mfi_macd/` と**同型**（iMFI→iRSI、価格 Typical、出来高
> なし）。先例の入出力アダプタ・描画仕様を踏襲しつつ、起点を iMFI から iRSI に置換し、
> **出来高（volume）を不要化**（OHLC ローダ）した。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `iRSI(RSIperiod, PRICE_TYPICAL)`（権威 Wilder）/ `iMAOnArray(MODE_EMA)`×3（Fast/Slow/Signal, 共有再利用）/ `Macd=Fast-Slow` / `Histogram=2.618×(Macd-Signal)` / `iStdDevOnArray`＋`iMAOnArray(MODE_SMA)`（σ 7 水準） |
| `src/rsimacd.py` | 成果物層（DataFrame 整形） | `MacdHistogramBuffer` / `MacdBuffer` / `SignalBuffer` 書き込み / StDevA1..A6 ＋ 50 水準 |
| `src/loader.py` | 入力アダプタ（CSV → OHLC） | `OnCalculate` 引数 high/low/close[] の供給（**volume 不要**） |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window（MACD 型・[0,100] 制約なし）+ DRAW_HISTOGRAM ＋ DRAW_LINE×2 + σ 水準線 7 本 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_histogram`×1 / `create_line`×2 / `horizontal_line`×7） |
| `tests/test_core.py` | core 計算の検証 | — |
| `tests/test_rsimacd.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake） | — |
| `demo.py` | デモ（合成 OHLC → PNG） | — |

> `src/plot.py` は matplotlib 依存のため、先例（profit_mfi_macd）同様 `src/__init__.py`
> の公開 API から除外する（matplotlib 未導入環境でも `import src` を壊さないため）。
> PNG 描画は `from src.plot import plot_rsimacd` で明示的に import する。

## 使い方

```python
from src import load_ohlc_csv, build_rsimacd, rsimacd_levels

df = load_ohlc_csv("ohlc.csv")              # open/high/low/close 必須（volume 不要・大小不問）
out = build_rsimacd(df, rsi_period=13, fast=4, slow=8, signal=4)
#   -> rsimacd_hist / rsimacd_macd / rsimacd_signal の 3 列（warm-up は 0 起点）
levels = rsimacd_levels(df, rsi_period=13, fast=4, slow=8, signal=4)
#   -> p1/p2/p3/m1/m2/m3/mid50(=50)
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_rsimacd
plot_rsimacd(df, "out.png", rsi_period=13, fast=4, slow=8, signal=4)
#   下段にヒストグラム＋RSIMACD/Signal 線＋σ 水準線 7 本（自動スケール）
```

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_histogram` / `create_line` /
`horizontal_line` を持つオブジェクトを duck typing で受ける。`lightweight_charts` は
import しない）:
```python
from src.lwc_chart import add_rsimacd
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_rsimacd(sub, df, rsi_period=13, fast=4, slow=8, signal=4)
#   ヒストグラム1本(rsimacd_hist)＋線2本(RSIMACD/Signal)＋水準線7本
```

### デモ / テスト
```bash
python demo.py                 # profit_rsi_macd_demo.png を生成
python -m pytest -q            # 全テスト
```

## 計算の要点

1. **価格 Typical → iRSI**: `price=(H+L+C)/3` に **権威 Wilder** の RSI を当てる。
   `neg==0→100`（all-up）、`neg==0 かつ pos==0 → 50`（flat window）、warm-up は 0。
   **価格 Typical 固定**（出来高は参照しない）。
2. **Fast/Slow/Signal EMA**: RSI / Macd 系列（warm-up 0 込み）を**共有 `moving_averages`
   の EMA** で平滑化（in-package 再実装しない）。
3. **Macd / Signal / Histogram**: `Macd=Fast-Slow`、`Signal=EMA(Macd,SignalEMA)`、
   `Histogram=2.618×(Macd-Signal)`（**係数 2.618 を厳密保持**）。
4. **σ 7 水準**: **Histogram（係数適用後）全系列**（warm-up の 0 込み）の
   `平均 ±{1,2,3}×母標準偏差（÷N）` ＋ 中央線 50。`iStdDevOnArray` /
   `iMAOnArray(MODE_SMA)` 相当。
5. **描画**: MACD 型・別ウィンドウ。元指標は `indicator_minimum/maximum` 未指定のため
   **[0,100] 制約なし（自動スケール）**。

## 元 MQL からの主な差分

- **warm-up は 0**: `i<rsi_period` の区間は元 iRSI 既定どおり 0 起点（NaN ではない）。
- **iRSI は権威 Wilder（`RSI.mq5`）準拠・flat→50** で再現（共有 `mql_builtins` の再公開）。
- **2.618 係数を厳密保持**（`Histogram=2.618×(Macd-Signal)`）。
- **統計に warm-up の 0 が混入する**: σ 7 水準（平均・母標準偏差）は **histogram
  (係数適用後)** の warm-up 0 を除外せず全系列で算出する。いずれも**原挙動の 1:1 再現**で
  あり "改善" しない（SPEC §9）。
- **EMA・適用価格（Typical）は共有層を流用**（重複排除）。**iRSI・σ 統計は in-package**
  （バッチ後 common 集約予定）。
- **volume は不要**: 元 `iRSI(...,PRICE_TYPICAL,...)` は出来高を参照しない（先例
  profit_mfi_macd は iMFI のため volume 必須だが本指標は OHLC で成立）。
- **bit-exact 非保証**: MT4 純正との厳密一致は参照 CSV が無いため保証しない。

詳細仕様は `SPEC.md` を参照。
