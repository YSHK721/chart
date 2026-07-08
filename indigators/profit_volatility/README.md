# PRO!fit_Volatility（Python 移植）

現足の価格 X と `period` 本前の価格 Y の乖離 `iVOLATILITY = pX[a]-pY[a-period]` を、
`inpPeriod=6` で 適用価格 X×Y の **49 系列**（X∈0..6 × Y∈0..6 = price_A × price_B）について
算出し、各値を「系列平均からの標準化距離」へ単位変換して合算した **符号付きオシレーター
（価格乖離の「温度」）** を、別ウィンドウのヒストグラムで可視化する MQL4 インジケーターの
Python 移植。元 `sample/MQL4/Indicators/PRO!fit_Volatility.mq4`（+ `ProfitSystem/PS.mqh` の
`iVOLATILITY`）準拠。移植方針は `indigators/PORTING_GUIDE.md` に従う。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 / PS.mqh の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `iVOLATILITY` / `PS_GetLevelCountValue` / `PS_GetUnitConversion` / `PS_GetAverage` / `PS_GetStandardDeviationValue` / `iBandsOnArray` / クランプ |
| `src/volatility.py` | 成果物層（DataFrame 整形） | クランプ済みレベルカウントバッファ（DRAW_HISTOGRAM）/ `PS_IndicatorLevelValueSet` |
| `src/loader.py` | 入力アダプタ（CSV → OHLC） | `CopyRates` / OnCalculate 引数の供給（volume 不要） |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window + DRAW_HISTOGRAM(DarkGreen, height100) + σ12 水準線 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_histogram` / `horizontal_line`×12） |
| `tests/test_core.py` | 計算の検証 | — |
| `tests/test_volatility.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake / σ12=12 本） | — |
| `demo.py` | デモ（合成 OHLC → PNG） | — |

## 使い方

```python
from src import load_ohlc_csv, build_volatility, volatility_levels

df = load_ohlc_csv("ohlc.csv")                 # open/high/low/close 必須（大小不問・volume 不要）
out = build_volatility(df, period=6)           # volatility_lc（クランプ済）
levels = volatility_levels(df, period=6)       # up_067..up_329 / dn_*
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_volatility
plot_volatility(df, "out.png", period=6)
```
（`src.plot` は matplotlib 依存を本パッケージ import に持ち込まないため `src/__init__` の
公開 API からは除外し、個別 import で利用する。）

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_histogram` / `horizontal_line` を
持つオブジェクトを duck typing で受ける。`lightweight_charts` は import しない）:
```python
from src import add_volatility
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_volatility(sub, df, period=6)              # ヒスト1本 + σ12 水準線12本
```

### デモ / テスト
```bash
python demo.py                 # profit_volatility_demo.png を生成（matplotlib 必要）
python -m pytest -q            # 41 tests
```

## 計算の要点（価格乖離の「温度」）

1. **iVOLATILITY**: 現足の価格 X と period 本前の価格 Y の乖離 `pX[a]-pY[a-period]`。
   warm-up（`a<period`）は元 OnCalculate が未計算のため 0（1:1 再現）。
2. **レベルカウント**: 49 系列（X×Y = price_A×price_B, 各 0..6）の iVOLATILITY を「平均から
   の σ 距離」へ単位変換して 49 回加算（**符号付き**。平均超で正・平均未満で負。mode 00 のみ
   初期化、残り 48 系統は加算）。
3. **σ12 水準 + クランプ**: 系列の SMA±σ·母標準偏差（σ=0.67〜3.29 を上下＝12 本）を水準線とし、
   ±3.29σ でクランプしてヒストグラム化。

## 元 MQL からの主な差分

- **iVOLATILITY の warm-up（最古 period 本）は 0**: 元 OnCalculate の
  `for(i<limit-inpPeriod)` が当該区間を未計算で 0 のまま残す挙動を 1:1 再現（ISSUE-002 解決済）。
- **WEIGHTED=(O+H+L+C)/4** は iVOLATILITY 固有式で、共有層 `common` 標準の (H+L+2C)/4 とは
  **異なる**ため、本 core は iVOLATILITY の素の MT4 式（median/typical/weighted）をそのまま実装。
- **`ps_level_count` / `compute_sigma_levels` は共有層 `profit_system` を再利用**（同一
  PS.mqh 関数の再現。profit_adx_needle と同一実装を参照）。`iVOLATILITY` は新規。
- **ビット完全一致は非保証**: MT4 実機の参照 CSV が無いため EMA 初期化・warm-up 区間は実機と
  厳密一致しない可能性がある（完全一致には参照 CSV による回帰固定が必要）。
- `int` 切り捨ては持ち込まず float 精度で実装（ガイド §4.1）。

詳細仕様は `SPEC.md` を参照。
