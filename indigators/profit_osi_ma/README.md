# PRO!fit_OSI_MA（Python 移植）

終値の移動平均（MA）からの**乖離率 MAKairi（%）**を、別ウィンドウの符号付き
ヒストグラムで可視化する MQL4 インジケーターの Python 移植。MA は MAMode で
SMA/EMA/SMMA/LWMA を切替（既定 EMA(21)）。元
`sample/MQL4/Indicators/PRO!fit_OSI_MA.mq4` 準拠。移植方針は
`indigators/PORTING_GUIDE.md` に従う。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `iMA(...,PRICE_CLOSE,i)` + `(Close[i+1]-ma)/ma*100`（共有 `moving_averages` 利用） |
| `src/osi_ma.py` | 成果物層（DataFrame 整形） | `SetIndexBuffer(0, MAKairi)` / `indicator_level1..4` |
| `src/loader.py` | 入力アダプタ（CSV → OHLC） | `Close[]`（OnCalculate 引数）の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window + DRAW_HISTOGRAM(Red) + 水準線 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_histogram` / `horizontal_line`） |
| `tests/test_core.py` | 計算の検証 | — |
| `tests/test_osi_ma.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake） | — |
| `demo.py` | デモ（合成 OHLC → PNG） | — |

## 使い方

```python
from src import load_ohlc_csv, build_osi_ma, osi_ma_levels

df = load_ohlc_csv("ohlc.csv")                 # close 必須（列名の大小不問）
out = build_osi_ma(df, ma_mode=1, ma_period=21)  # osi_ma_kairi 列（乖離率%）
levels = osi_ma_levels()                        # {lvl_1:1.0, lvl_05:0.5, lvl_-05:-0.5, lvl_-1:-1.0}
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_osi_ma   # matplotlib 必須（src パッケージ本体は numpy/pandas のみ依存）
plot_osi_ma(df, "out.png", ma_mode=1, ma_period=21)
```

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_histogram` / `horizontal_line` を
持つオブジェクトを duck typing で受ける。`lightweight_charts` は本パッケージから import
しない）:
```python
from src.lwc_chart import add_osi_ma
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_osi_ma(sub, df, ma_mode=1, ma_period=21)
```
> 実描画には GUI/ブラウザ（Xvfb 等）が必要。ヘッドレス CI ではテストが Fake チャートで
> 完結するため lightweight-charts のインストールは不要。

### デモ / テスト
```bash
python demo.py                 # profit_osi_ma_demo.png を生成（matplotlib 必須）
python -m pytest -q            # 24 tests（matplotlib/lightweight-charts 不要）
```

## 計算の要点（乖離率 MAKairi）

1. **MA**: 終値の移動平均。MAMode で SMA(0) / EMA(1) / SMMA(2) / LWMA(3) を切替（既定
   EMA, 期間 21）。共有 `moving_averages`（`MovingAverages.mqh` 移植）の on_buffer を利用。
2. **乖離率**: `kairi[a] = (close[a-1] - ma_a) / ma_a * 100`（**符号付き**。MA より上で
   正・下で負）。`ma==0`（ゼロ除算ガード／未確定）と最古バー（a==0）は NaN。
3. **水準線**: 1 / 0.5 / -0.5 / -1（行き過ぎ／戻りの参照線）。

## 元 MQL からの主な差分

- **分子の 1 本ずれを 1:1 再現（最重要）**: 元コードは `MAKairi[i] = (Close[i+1] - ma)/ma*100`
  で、MA を足 `i` で算出する一方、分子に `Close[i+1]`（1 本古い終値）を用いる非対称性が
  ある。本移植はこれを**原挙動として未修正のまま再現**し、昇順で `close[a-1]` とする
  （バグか意図かは断定せず再現を保証）。詳細・固定テストは `SPEC.md` §9。
- **丸めを持ち込まない**: 元に `NormalizeDouble` は無く float 精度で算出（ガイド §4.1）。
- **ビット完全一致は非保証**: MT4 実機の参照 CSV が無く、MA の warm-up/seed は実機と
  厳密一致しない可能性がある（完全一致には参照 CSV による回帰固定が必要）。

詳細仕様は `SPEC.md` を参照。
