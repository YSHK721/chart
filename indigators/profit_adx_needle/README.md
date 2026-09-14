# PRO!fit_ADX_NEEDLE（Python 移植）

ADX（平均方向性指数）を `inpPeriod=6` で算出し、各値を「系列平均からの標準化距離」へ
単位変換して合算した**符号付きオシレーター（市場の「温度」）**を、別ウィンドウの
ヒストグラムで可視化する MQL4 インジケーターの Python 移植。元
`sample/MQL4/Indicators/PRO!fit_ADX_NEEDLE.mq4`（+ `ProfitSystem/PS.mqh`）準拠。
移植方針は `indigators/PORTING_GUIDE.md` に従う。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 / PS.mqh の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `iADX` / `PS_GetLevelCountValue` / `PS_GetUnitConversion` / `PS_GetAverage` / `PS_GetStandardDeviationValue` / `iBandsOnArray` / クランプ |
| `src/needle.py` | 成果物層（DataFrame 整形） | `ExtBufferLevelCount`（DRAW_HISTOGRAM）/ `PS_IndicatorLevelValueSet` |
| `src/loader.py` | 入力アダプタ（CSV → OHLC） | `CopyRates` / OnCalculate 引数の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window + DRAW_HISTOGRAM(DarkGreen) + レベル線 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_histogram` / `horizontal_line`） |
| `tests/test_core.py` | 計算の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake） | — |
| `demo.py` | デモ（合成 OHLC → PNG） | — |

## 使い方

```python
from src import load_ohlc_csv, build_adx_needle, needle_levels

df = load_ohlc_csv("ohlc.csv")           # high/low/close 必須（列名の大小不問）
out = build_adx_needle(df, period=6)     # adx_needle / adx_level_count / adx
levels = needle_levels(df, period=6)     # up_067..up_329 / dn_* / upper_clamp / lower_clamp
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_adx_needle
plot_adx_needle(df, "out.png", period=6)
```

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_histogram` / `horizontal_line` を
持つオブジェクトを duck typing で受ける）:
```python
from src.lwc_chart import add_adx_needle
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_adx_needle(sub, df, period=6)
```

### デモ / テスト
```bash
python demo.py                 # profit_adx_needle_demo.png を生成
python -m pytest -q            # 26 tests
```

## 計算の要点（市場の「温度」）

1. **ADX**（MetaQuotes 版 MT4 `iADX` 準拠）: +DM/-DM を High/Low、TR を High/Low/Close から
   求め、+SDI/-SDI/DX/ADX を **EMA（α=2/(N+1)）** で平滑。ADX ∈ [0,100]。
2. **レベルカウント**: `≒ 7×(adx-平均)/std`（**符号付き**。平均超で正・平均未満で負）。
   トレンドの方向性が平均より明確なほど 0 から離れる。
3. **σ 水準 + クランプ**: 系列の SMA±σ·母標準偏差（σ=0.67〜3.29）を水準線とし、±3.29σ で
   クランプしてヒストグラム化。

## 元 MQL からの主な差分

- **applied_price は出力に影響しない（vestigial）**: MetaQuotes 版 MT4 `iADX` は方向性を
  High/Low から算出し `applied_price` を実質使用しない（MQL5 で削除された理由）。よって元の
  7 種呼び出しは同一 ADX を返し、レベルカウントは 7×。詳細・出典は `SPEC.md` §9。
- **ビット完全一致は非保証**: MT4 実機の参照 CSV が無いため EMA 初期化・warm-up 区間は実機と
  厳密一致しない可能性がある（完全一致には参照 CSV による回帰固定が必要）。
- `int` 切り捨ては持ち込まず float 精度で実装（ガイド §4.1）。

詳細仕様は `SPEC.md` を参照。
