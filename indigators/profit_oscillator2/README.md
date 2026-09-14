# PRO!fitOscillator（Python 移植）

8 つのサブオシレーター（**iRSI / iWPR(+100) / iMFI / MAROD(Typical/High/Low) /
iStochastic(main/signal)**）を `funLevelCount`（span=100 固定）で「単位距離」へ変換し
**加重 1/2/2/10/10/10/1/1** で合算した**符号付きレベルカウント**と、その
**Spearman RCI**（順位相関指標）を、別ウィンドウのヒストグラム 1 本＋線 1 本で可視化する
MQL4 インジケーターの Python 移植。元 `sample/MQL4/Indicators/PRO!fitOscillator.mq4`
（2015, PRO!fit Investars）準拠。

既存 `profit_oscillator` とは完全分離（`compute_oscillator2_full` / `Oscillator2Result` /
`oscillator2_lc` 等の命名で衝突回避）。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy ＋ 共有層のみ） | サブ 8 オシレーター / funLevelCount / 加重集計 / σ6 水準 / Spearman RCI |
| `src/oscillator2.py` | 成果物層（DataFrame 整形） | `ExtBufferLevelCount`（`oscillator2_lc`）/ `ExtBufferRCI`（`oscillator2_rci`） |
| `src/loader.py` | 入力アダプタ（CSV → OHLCV） | OnCalculate 引数 high/low/close/**volume** の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window + DRAW_HISTOGRAM(DarkGreen) + DRAW_LINE(clrLime) + σ6 水準線 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_histogram` / `create_line` / `horizontal_line`） |
| `tests/test_core.py` | 計算の検証 | — |
| `tests/test_oscillator2.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake） | — |
| `demo.py` | デモ（合成 OHLCV → PNG） | — |

## 使い方

```python
from src import load_ohlcv_csv, build_oscillator2, oscillator2_levels

df = load_ohlcv_csv("ohlcv.csv")   # open/high/low/close/volume 必須（列名大小不問）
out = build_oscillator2(df, osc_period=6, stc_slow=6, ma_period=60, rci_period=12, direction=False)
# out: oscillator2_lc（レベルカウント）/ oscillator2_rci（RCI）の 2 列
levels = oscillator2_levels(df)    # up_165/up_196/up_258/dn_165/dn_196/dn_258 + sub_min/sub_max
```

### 描画（matplotlib / PNG）

```python
from src.plot import plot_oscillator2
plot_oscillator2(df, "out.png")    # 下段 y 軸 sub_min〜sub_max（LC クランプ無し）
```

### 描画（lightweight-charts）

別ウィンドウ型のため subchart を用意して渡す（`create_histogram` / `create_line` /
`horizontal_line` を持つオブジェクト。`lightweight_charts` は本パッケージ側で import しない）。

```python
from src import add_oscillator2
add_oscillator2(subchart, df, draw_levels=True)
# ヒストグラム 1 本（oscillator2_lc）＋ RCI 線 1 本（oscillator2_rci）＋ σ6 水準線 6 本
```

## パラメータ（元 input 既定）

| 引数 | 既定 | 元 input | 意味 |
|---|---|---|---|
| `osc_period` | 6 | `inpPeriodOscillator` | サブオシレーター期間 |
| `stc_slow` | 6 | `inpPeriodSTC_SLOW` | iStochastic slowing ＝ D 期間 |
| `ma_period` | 60 | `inpPeriodMA` | MAROD の EMA 期間 |
| `rci_period` | 12 | `inpPeriodRCI` | RCI 期間 |
| `direction` | False | `direction` | RCI ソート方向 |

## テスト

```bash
cd indicators/profit_oscillator2 && python -m pytest -q
```

詳細な計算定義・元 MQL 差分は [SPEC.md](./SPEC.md) を参照。
