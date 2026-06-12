# PRO!fit_Arctan（Python 移植）

移動平均の隣接差 `MA[i]-MA[i-1]` を `MathArctan` で角度（度）へ変換したオシレーター
`iARCTAN` を `inpPeriod=6` / `inpTypeMA=1`（EMA）/ `BarWidth=0.1` で 7 種の適用価格について
算出し、各値を「系列平均からの標準化距離」へ単位変換して合算した**符号付きオシレーター
（市場の「温度」）**を、別ウィンドウのヒストグラムで可視化する MQL4 インジケーターの
Python 移植。元 `sample/MQL4/Indicators/PRO!fit_Arctan.mq4`（+ `ProfitSystem/PS.mqh` の
`iARCTAN`）準拠。移植方針は `indigators/PORTING_GUIDE.md` に従う。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 / PS.mqh の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy + 共有層のみ） | `iARCTAN` / `PS_GetLevelCountValue` / `PS_GetUnitConversion` / `PS_GetAverage` / `PS_GetStandardDeviationValue` / `iBandsOnArray` / クランプ |
| `src/arctan.py` | 成果物層（DataFrame 整形） | クランプ済みレベルカウントバッファ（DRAW_HISTOGRAM）/ `PS_IndicatorLevelValueSet` |
| `src/loader.py` | 入力アダプタ（CSV → OHLC） | `CopyRates` / OnCalculate 引数の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window + DRAW_HISTOGRAM(DarkGreen, height100) + σ12 水準線 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_histogram` / `horizontal_line`×12） |
| `tests/test_core.py` | 計算の検証 | — |
| `tests/test_arctan.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake / σ12=12 本） | — |
| `demo.py` | デモ（合成 OHLC → PNG） | — |

## 使い方

```python
from src import load_ohlc_csv, build_arctan, arctan_levels

df = load_ohlc_csv("ohlc.csv")                          # open/high/low/close 必須（大小不問）
out = build_arctan(df, period=6, ma_method=1, bar_width=0.1)   # arctan_lc（クランプ済）
levels = arctan_levels(df, period=6, ma_method=1, bar_width=0.1)  # up_067..up_329 / dn_*
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_arctan
plot_arctan(df, "out.png", period=6, ma_method=1, bar_width=0.1)
```
（`src.plot` は matplotlib 依存を本パッケージ import に持ち込まないため `src/__init__` の
公開 API からは除外し、個別 import で利用する。）

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_histogram` / `horizontal_line` を
持つオブジェクトを duck typing で受ける。`lightweight_charts` は import しない）:
```python
from src import add_arctan
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_arctan(sub, df, period=6, ma_method=1, bar_width=0.1)   # ヒスト1本 + σ12 水準線12本
```

### デモ / テスト
```bash
python demo.py                 # profit_arctan_demo.png を生成（matplotlib 必要）
python -m pytest -q            # 39 tests
```

## 計算の要点（市場の「温度」）

1. **iARCTAN**: MA の隣接差を `(atan(MA[i]-MA[i-1])/bar_width)*(180/π)` で角度化（B 未確定→0）。
   MA 方式は `inpTypeMA`（0=SMA/1=EMA/2=SMMA/3=LWMA, 既定 EMA）。
2. **レベルカウント**: 7 適用価格（W/T/M/H/L/O/C）の iARCTAN を「平均からの σ 距離」へ単位
   変換して 7 回加算（**符号付き**。平均超で正・平均未満で負）。iARCTAN は applied_price で
   MA 入力が変わるため 7 系統は別系列＝単純な 7 倍ではない。
3. **σ12 水準 + クランプ**: 系列の SMA±σ·母標準偏差（σ=0.67〜3.29 を上下＝12 本）を水準線とし、
   ±3.29σ でクランプしてヒストグラム化。

## 元 MQL からの主な差分

- **applied_price は計算へ入る**（ADX_NEEDLE と対比）: iARCTAN は MA 入力を 7 適用価格で
  切り替えるため 7 系統が別系列。`applied_price` は共有層 `common`（CLOSE=1 系）から供給。
- **`ps_level_count` / `compute_sigma_levels` は profit_adx_needle の複製**（同一 PS.mqh
  関数の再現）。バッチ後 `common/` 集約予定。同一性維持のため本タスクでは共通化しない。
- **`iARCTAN` は 8 引数（`digits` 無視）／ B 未確定→0 を 1:1 再現**。
- **ビット完全一致は非保証**: MT4 実機の参照 CSV が無いため EMA 初期化・warm-up 区間は実機と
  厳密一致しない可能性がある（完全一致には参照 CSV による回帰固定が必要）。
- `int` 切り捨ては持ち込まず float 精度で実装（ガイド §4.1）。

詳細仕様は `SPEC.md` を参照。
