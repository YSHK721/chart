# PRO!fit_Oscillator（Python 移植）

18 本のサブオシレーター系列（iRSI×7 / iStochastic×2 / iMFI×1 / iRVI×1 / iMARD×7）を
それぞれ「系列平均からの標準化距離（σ 距離）」へ単位変換して合算した **符号付き複合
オシレーター（市場の「温度」）** を、別ウィンドウのヒストグラム 1 本で可視化する MQL4
インジケーターの Python 移植。元 `sample/MQL4/Indicators/PRO!fit_Oscillator.mq4`
（+ `ProfitSystem/PS.mqh` の `iRVI` / `iMARD` / `PS_GetLevelCountValue` 等）準拠。
移植方針は `indigators/PORTING_GUIDE.md` に従う。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 / PS.mqh の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy + 共有層のみ） | `iRSI`/`iStochastic`/`iMFI`/`iRVI`/`iMARD` / `PS_GetLevelCountValue` / `PS_GetUnitConversion` / `PS_GetAverage` / `PS_GetStandardDeviationValue` / `iBandsOnArray` / クランプ |
| `src/oscillator.py` | 成果物層（DataFrame 整形） | クランプ済みレベルカウントバッファ（DRAW_HISTOGRAM）/ `PS_IndicatorLevelValueSet` |
| `src/loader.py` | 入力アダプタ（CSV → OHLCV, **volume 必須**） | `CopyRates` / OnCalculate 引数の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window + DRAW_HISTOGRAM(DarkGreen, height100) + σ12 水準線 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_histogram` / `horizontal_line`×12） |
| `tests/test_core.py` | 計算の検証 | — |
| `tests/test_oscillator.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake / σ12=12 本） | — |
| `demo.py` | デモ（合成 OHLCV → PNG） | — |

## 使い方

```python
from src import load_ohlcv_csv, build_oscillator, oscillator_levels

df = load_ohlcv_csv("ohlcv.csv")                    # open/high/low/close/volume 必須（大小不問）
out = build_oscillator(df, period_a=6, period_b=60) # oscillator_lc（±3.29σ クランプ済）
levels = oscillator_levels(df, period_a=6, period_b=60)  # up_067..up_329 / dn_*（σ12）
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_oscillator
plot_oscillator(df, "out.png", period_a=6, period_b=60)
```
（`src.plot` は matplotlib 依存を本パッケージ import に持ち込まないため `src/__init__` の
公開 API からは除外し、個別 import で利用する。matplotlib 未導入環境でも `import src` /
テストは壊れない。）

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_histogram` / `horizontal_line` を
持つオブジェクトを duck typing で受ける。`lightweight_charts` は import しない）:
```python
from src import add_oscillator
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_oscillator(sub, df, period_a=6, period_b=60)   # ヒスト1本 + σ12 水準線12本
```

### デモ / テスト
```bash
python demo.py                 # profit_oscillator_demo.png を生成（matplotlib 必要）
python -m pytest -q            # 40 tests
```

## 計算の要点（18 系列の市場「温度」）

1. **18 サブ系列**（`compute_level_count`, period_a=6 / period_b=60）:
   - iRSI × 7（適用価格 W/T/M/H/L/O/C, period_a）
   - iStochastic × 2（MAIN / SIGNAL とも slowing=1/Dperiod=1 で生 %K に帰着＝同一配列を 2 回加算）
   - iMFI × 1（period_a, **volume を要する**）
   - iRVI × 1（period_a, 権威 RVI.mq5 の MAIN, 三角加重 1,2,2,1）
   - iMARD × 7（period_b, EMA 固定, 適用価格 7 種）
2. **レベルカウント**: 各系列を `PS_GetLevelCountValue`（平均 ± 3.29σ·EMA標準偏差を基準距離
   329 で σ 距離化, `NormalizeDouble(_,5)`）で加算。**1 系統目（iRSI_WEIGHTED）のみ初期化**。
   平均超で正・平均未満で負の **符号付き** 合算。
3. **σ12 水準 + クランプ**: 合算系列の SMA±σ·母標準偏差（σ=0.67〜3.29 を上下＝12 本）を水準線とし、
   ±3.29σ でクランプしてヒストグラム化（`oscillator_lc`）。

## 元 MQL からの主な差分（詳細は SPEC §9）

- **iMARD WEIGHTED の非対称を 1:1 再現**: 分子 `(O+H+L+C)/4`・分母 `EMA((H+L+2C)/4)`
  という原コードの非対称（PS.mqh L1316）を **修正せず** 再現する。他 6 価格は対称。
- **iRVI / iMARD は新規実装**（権威 `RVI.mq5` / `PS.mqh iMARD`）。三角加重・warm-up i<period+2・
  EMA 固定を 1:1 再現。
- **`iRSI` / `iMFI` / `iStochastic` は共有 `mql_builtins`、`ps_level_count` /
  `compute_sigma_levels` は共有 `profit_system` の再公開**（共有層の正準実装に一本化済み）。
- **iStochastic 2 モードは生 %K に帰着**（slowing=1/Dperiod=1）。
- **applied_price は共有層 `common`**（CLOSE=1 系）から供給（iRSI / iMARD の 7 系統）。
- **ビット完全一致は非保証**: MT4 実機の参照 CSV が無いため EMA 初期化・warm-up 区間は実機と
  厳密一致しない可能性がある（完全一致には参照 CSV による回帰固定が必要）。
- `int` 切り捨ては持ち込まず float 精度で実装（ガイド §4.1）。

詳細仕様は `SPEC.md` を参照。
