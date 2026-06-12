# PRO!fit_HLBand（Python 移植）

High-Close / Low-Close の絶対距離 `dist_high = |high - close|` / `dist_low = |low - close|`
の系列全体の `平均` と `母標準偏差帯`（dev = 0.67 / 1.65 / 1.96 / 2.58）を算出し、起点終値
`close_ref = close[-2]`（元 `iClose(...,1)`）へ High 側距離帯を**加算**・Low 側距離帯を**減算**
して価格軸へ投影した overlay バンド 8 本を、メインチャートに重ねて描く MQL4 インジケーターの
Python 移植。

- **overlay 専用（メインチャート重畳）**: 元 `indicator_chart_window`。**8 本の水平バンド線**
  （上側 4 = up_067/up_165/up_196/up_258・下側 4 = dn_067/dn_165/dn_196/dn_258, LimeGreen）。
  **separate ウィンドウ・ヒストグラム・プロット用バッファは持たない**（元来無い）。

元 `sample/MQL4/Indicators/PRO!fit_HLBand.mq4`（Copyright 2017, PRO!fit Investars）準拠。
移植方針は `indigators/PORTING_GUIDE.md` に従う。本指標に**計算 input は無い**（元 `input` は
`inpSymbol`/`inpTimeFrame` のシンボル・時間足参照引数のみで、計算 period ではない）。

> **既存 `profit_hlband`（アンダースコア無し）とは別物**。`profit_hlband` は高安レンジ
> `high - low` を**最新足 H/L** へ投影し separate＋overlay の二系統を描くのに対し、本
> `profit_hl_band` は**距離 `|H-C|`/`|L-C|`** を**起点 close[-2]** へ投影し **overlay 専用**で
> 描く。計算定義・起点・描画系統が異なる。詳細は `SPEC.md` §9。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `|H-C|`/`|L-C|` 距離 / `iBandsOnArray`（母σ帯）/ `iClose(1)` 起点への 8 投影 |
| `src/hl_band.py` | 成果物層（DataFrame 整形） | 距離 2 列（`ResBufferDivisionOpenHigh/Low`）/ 8 バンド + close_ref 辞書 |
| `src/loader.py` | 入力アダプタ（CSV → OHLC） | `CopyRates` / OnCalculate 引数の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG・overlay） | 価格 + 水平バンド線 8 本（axhline, LimeGreen） |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts, duck typing） | `horizontal_line` ×8（OBJ_TREND 8 本の再表現） |
| `tests/test_core.py` | core 計算の検証 | — |
| `tests/test_hl_band.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake チャート・水平線 8 本） | — |
| `demo.py` | デモ（合成 OHLC → PNG） | — |

> `src/plot.py` は matplotlib 依存のため、先例（profit_hlband）同様 `src/__init__.py` の公開
> API から除外する（matplotlib 未導入環境でも `import src` を壊さないため）。PNG 描画は
> `from src.plot import plot_hl_band` で明示的に import する。

## 使い方

```python
from src import load_ohlc_csv, build_hl_band, hl_band_levels

df = load_ohlc_csv("ohlc.csv")     # open/high/low/close 必須（列名の大小不問）
dists = build_hl_band(df)          # hlband_dist_high / hlband_dist_low の 2 列（warm-up/NaN なし）
levels = hl_band_levels(df)        # {up_067..up_258, dn_067..dn_258, close_ref}
```

### 描画（matplotlib / PNG・overlay）
```python
from src.plot import plot_hl_band
plot_hl_band(df, "out.png")        # 価格 + 水平バンド線 8 本（separate ペインなし）
```

### 描画（lightweight-charts）
メイン chart にバンド 8 本を追加する（`horizontal_line` を持つオブジェクトを duck typing で
受ける。`lightweight_charts` は import しない）。価格系列の描画は呼び出し側前提:
```python
from src.lwc_chart import add_hl_band

# メインチャートへ水平線 8 本（up_*/dn_*）
add_hl_band(chart, df)
```

### デモ / テスト
```bash
python demo.py                 # profit_hl_band_demo.png を生成
python -m pytest -q            # 全 PASS
```

## 計算の要点

1. **距離**: `dist_high[i] = |high[i] - close[i]|`、`dist_low[i] = |low[i] - close[i]|`
   （全バー、warm-up なし）。
2. **距離帯（母σ÷N）**: `band_upper(dist, dev) = mean(dist) + dev×sigma`、
   `sigma = sqrt(mean((x-mean)^2))`（÷N, `iBandsOnArray` 準拠）。dev ∈ {0.67,1.65,1.96,2.58}。
3. **起点**: `close_ref = close[-2]`（元 `iClose(...,1)` = 1 本前の終値。最新足ではない）。
4. **8 投影**: `up_* = close_ref + band_upper(dist_high, dev)`（加算）、
   `dn_* = close_ref - band_upper(dist_low, dev)`（減算）。

## 元 MQL からの主な差分

- **`close[-2]` 起点（`Close[1]`）の 1:1 再現**: 最新足 `close[-1]` ではなく 1 本前の終値。
- **母σ÷N の 1:1 再現**: `iBandsOnArray` の標準偏差は母分散（÷N）。標本σ（ddof=1）は使わない。
- **MT4 描画オブジェクト（OBJ_TREND）を水平線で再表現**: `ObjectCreate`/`ObjectDelete` の
  ライフサイクル管理は移植対象外。投影値 8 本を水平線として描く（SPEC §2）。
- **既存 `profit_hlband` とは別物**: 距離 `|H-C|`/`|L-C|`・起点 close[-2]・overlay 専用
  （vs. `profit_hlband`: レンジ `H-L`・最新 H/L 起点・separate＋overlay）。
- **計算 input なし**: 元 `input` は参照用 `inpSymbol`/`inpTimeFrame` のみで計算 period でない。
- **`N>=2` ガード**: `close[-2]` が定義不能な `N<2` に対し `ValueError`（core）。
- **MT4 純正との bit-exact は非保証**: 参照 CSV が無いため厳密一致は保証しない。

詳細仕様は `SPEC.md` を参照。
