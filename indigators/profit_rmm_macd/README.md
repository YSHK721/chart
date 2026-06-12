# PRO!fitRMMMACD（Python 移植）

RMM レベルカウント（iRSI / iWPR / iMFI / MAROD の 4 オシレーターを `funLevelCount` で
採点・合算した `level_count`）を起点に、その `FastEMA=4` / `SlowEMA=8` の EMA から
**MACD**（**`Macd=Slow-Fast`**）と **Signal**（`EMA(Macd, SignalEMA=4)`）を求め、
ヒストグラム **`Histogram=Macd-Signal`（係数なし）**を別ウィンドウ（**MACD 型・
[0,100] 制約なし**）に描く MQL4 インジケーターの Python 移植。**σ 水準線は持たない**。
元 `sample/MQL4/Indicators/PRO!fitRMMMACD.mq4`（Copyright (c) PRO!fit Investars）準拠。
移植方針は `indigators/PORTING_GUIDE.md` に従う。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy ＋ 共有） | level_count（profit_rmm 複製）/ `iMAOnArray(MODE_EMA)`×3（Fast/Slow/Signal, 共有再利用）/ `Macd=Slow-Fast`（L272）/ `Histogram=Macd-Signal`（L280・係数なし） |
| `src/rmmmacd.py` | 成果物層（DataFrame 整形） | `MacdHistogramBuffer` / `MacdBuffer` / `SignalBuffer` 書き込み（σ 水準なし） |
| `src/loader.py` | 入力アダプタ（CSV → OHLCV） | `OnCalculate` 引数 high/low/close/**volume**[] の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window（MACD 型・[0,100] 制約なし）+ DRAW_HISTOGRAM ＋ DRAW_LINE×2（**水準線なし**） |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_histogram`×1 / `create_line`×2、**`horizontal_line` なし**） |
| `tests/test_core.py` | core 計算の検証 | — |
| `tests/test_rmmmacd.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake・水平線 0 本） | — |
| `demo.py` | デモ（合成 OHLCV → PNG） | — |

> `src/plot.py` は matplotlib 依存のため、先例（profit_mfi_macd）同様 `src/__init__.py`
> の公開 API から除外する（matplotlib 未導入環境でも `import src` を壊さないため）。
> PNG 描画は `from src.plot import plot_rmmmacd` で明示的に import する。

## 使い方

```python
from src import load_ohlcv_csv, build_rmmmacd

df = load_ohlcv_csv("ohlcv.csv")            # open/high/low/close/volume 必須（大小不問）
out = build_rmmmacd(df, osc_period=6, ma_period=6, fast=4, slow=8, signal=4)
#   -> rmmmacd_hist / rmmmacd_macd / rmmmacd_signal の 3 列（warm-up 起点込み）
#   σ 水準は無い（levels 関数なし）
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_rmmmacd
plot_rmmmacd(df, "out.png", osc_period=6, ma_period=6, fast=4, slow=8, signal=4)
#   下段にヒストグラム＋RMMWMACD/Signal 線（水準線なし・自動スケール）
```

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_histogram` / `create_line` を
持つオブジェクトを duck typing で受ける。`lightweight_charts` は import しない。
**`horizontal_line` は呼ばない**）:
```python
from src.lwc_chart import add_rmmmacd
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_rmmmacd(sub, df, osc_period=6, ma_period=6, fast=4, slow=8, signal=4)
#   ヒストグラム1本(rmmmacd_hist)＋線2本(RMMWMACD/Signal)、水準線なし
```

### デモ / テスト
```bash
python demo.py                 # profit_rmm_macd_demo.png を生成（matplotlib 必要）
python -m pytest -q            # 全テスト
```

## 計算の要点

1. **level_count**: iRSI / iWPR / iMFI / MAROD を `funLevelCount`（4 ケース採点）で
   採点・合算（**profit_rmm の verbatim 複製**。同一入力で
   `profit_rmm.compute_rmm(...).level_count` と bit-for-bit 一致）。
2. **Fast/Slow/Signal EMA**: level_count / Macd 系列（warm-up 込み）を**共有
   `moving_averages` の EMA** で平滑化（in-package 再実装しない）。
3. **Macd / Signal / Histogram**: **`Macd=Slow-Fast`**（L272・他 MACD 変種の Fast-Slow
   と符号逆）、`Signal=EMA(Macd,SignalEMA)`、**`Histogram=Macd-Signal`（係数なし・
   L280。RMMMACD のみ 2.618 係数を掛けない）**。
4. **σ 水準**: **無い**（元 `funIndicatorSet` は OnCalculate から呼ばれない）。
5. **描画**: MACD 型・別ウィンドウ。元指標は `indicator_minimum/maximum` 未指定のため
   **[0,100] 制約なし（自動スケール）**。水準線なし。

## 元 MQL からの主な差分

- **`Macd=Slow-Fast`**（他 MACD 変種の `Fast-Slow` と符号が逆・元 L272）を 1:1 再現。
- **`Histogram=Macd-Signal`（係数なし）**（RMMMACD のみ 2.618 係数を掛けない・元 L280）
  を 1:1 再現。
- **σ 水準線なし**（元 `funIndicatorSet` 未呼出）。DTO に levels フィールドなし・levels
  関数なし・`horizontal_line` 呼出なし。
- **level_count は profit_rmm の verbatim 複製**（warm-up・負MF==0→100・同値非対称・
  funLevelCount のゼロ割非ガード等を含め原挙動の 1:1 再現）。
- **EMA は共有 moving_averages を流用**（重複排除）。**level_count パイプラインは
  in-package 複製**（同一性維持のため共通化しない。バッチ後 common 集約予定）。
- **volume の tick/実出来高 は CSV 列定義依存で bit-exact 非保証**。MT4 純正との厳密
  一致は参照 CSV が無いため保証しない。

詳細仕様は `SPEC.md` を参照。
