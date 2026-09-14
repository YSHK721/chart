# PRO!fitHLBand（Python 移植）

高安レンジ `range = high - low` の系列全体の `平均` と `母標準偏差帯`（+1.65σ / +1.96σ /
+2.58σ）を算出し、二系統で描画する MQL4 インジケーターの Python 移植:

- **(A) 別ウィンドウ（separate）**: `hl_range` のヒストグラム（DRAW_HISTOGRAM, clrLime, width2）
  ＋ σ 水準線 4 本（avg/b165/b196/b258, SOLID, グレー C'84,84,84'）。subwindow 範囲
  MIN=0 / MAX=b196*2。
- **(B) メインチャート（overlay）**: 最新足の High から各帯を減算・最新足の Low へ各帯を加算
  して投影した水平バンド 8 本（high_*/low_*, LimeGreen。元 OBJ_TREND 8 本）。

元 `sample/MQL4/Indicators/PRO!fitHLBand.mq4`（Copyright 2015, PRO!fit Investars）準拠。
移植方針は `indigators/PORTING_GUIDE.md` に従う。本指標に **input パラメータは無い**（元コードに
`input` 宣言が存在しない）。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `range` / `iMAOnArray`（平均）/ `iBandsOnArray`（母σ帯）/ 最新 H/L 投影 / INDICATOR_MIN/MAX |
| `src/hlband.py` | 成果物層（DataFrame 整形 + 空入力ガード） | `ExtVOLBuffer`（DRAW_HISTOGRAM）/ `StcLCStdDevArray[1..4]` / 8 投影辞書 |
| `src/loader.py` | 入力アダプタ（CSV → OHLC） | `CopyRates` / OnCalculate 引数の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate ヒストグラム＋水準線4本 ＋ overlay 水平線8本 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts, duck typing） | `create_histogram` / `horizontal_line`（separate / overlay 別関数） |
| `tests/test_core.py` | core 計算の検証 | — |
| `tests/test_hlband.py` | 成果物層・空入力ガードの検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake チャート） | — |
| `demo.py` | デモ（合成 OHLC → PNG） | — |

> `src/plot.py` は matplotlib 依存のため、先例（profit_stc）同様 `src/__init__.py` の公開 API
> から除外する（matplotlib 未導入環境でも `import src` を壊さないため）。PNG 描画は
> `from src.plot import plot_hlband` で明示的に import する。

## 使い方

```python
from src import load_ohlc_csv, build_hlband, hlband_levels, hlband_price_bands

df = load_ohlc_csv("ohlc.csv")        # open/high/low/close 必須（列名の大小不問）
ranges = build_hlband(df)             # hl_range 1 列（warm-up/NaN なし）
levels = hlband_levels(df)            # {avg, b165, b196, b258, sub_min(=0), sub_max(=b196*2)}
bands  = hlband_price_bands(df)       # high_*/low_* の overlay 8 本
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_hlband
plot_hlband(df, "out.png")            # 上段=overlay 水平線8本 / 下段=ヒストグラム＋水準線4本
```

### 描画（lightweight-charts）
二系統を別関数で追加する（`create_histogram` / `horizontal_line` を持つオブジェクトを
duck typing で受ける。`lightweight_charts` は import しない）:
```python
from src.lwc_chart import add_hlband_separate, add_hlband_overlay

# (A) 別ウィンドウ: ヒストグラム1本（name=hl_range）＋水準線4本
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_hlband_separate(sub, df)

# (B) メインチャート: 水平線8本（high_*/low_*）
add_hlband_overlay(chart, df)
```

### デモ / テスト
```bash
python demo.py                 # profit_hlband_demo.png を生成
python -m pytest -q            # 全 PASS
```

## 計算の要点

1. **レンジ**: `range[i] = high[i] - low[i]`（全バー、warm-up なし）。
2. **統計（母σ÷N）**: `avg = mean(range)`、`b{165,196,258} = avg + {1.65,1.96,2.58}×sigma`、
   `sigma = sqrt(mean((x-avg)^2))`（÷N, `iBandsOnArray` 準拠）。
3. **最新 H/L 投影 8 本**: `high_* = high[-1] - 各帯`（減算）、`low_* = low[-1] + 各帯`（加算）。
4. **subwindow 範囲**: `sub_min = 0`（INDICATOR_MINIMUM）〜 `sub_max = b196*2`（INDICATOR_MAXIMUM）。

## 元 MQL からの主な差分

- **母σ÷N の 1:1 再現**: `iBandsOnArray` の標準偏差は母分散（÷N）。標本σ（ddof=1）は使わない。
- **MT4 描画オブジェクト（OBJ_TREND）を水平線で再表現**: `ObjectCreate`/`ObjectDelete` の
  ライフサイクル管理は移植対象外。投影値 8 本を水平線として描く（SPEC §2）。
- **母σ統計の共有 common 化は別タスク（rule-of-three）**: range の全系列「平均 ±dev×母σ」帯は
  `profit_adx_needle`・`profit_stc` に続き本指標で 3 例目。共有層への抽出が妥当な段階だが、本
  タスクのスコープ外のため**別タスクとして提案**し、現状は in-package（`src/core.py`）に留める
  （YAGNI／スコープ限定）。
- **空入力ガード**: 空 DataFrame に対し成果物層で明示的 `ValueError`（core は不変）。
- **input パラメータなし**: 元コードに `input` が無いため移植先も引数を追加しない。
- **MT4 純正との bit-exact は非保証**: 参照 CSV が無いため厳密一致は保証しない。

詳細仕様は `SPEC.md` を参照。
