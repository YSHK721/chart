# PRO!fitSTC（Python 移植）

終値の高安レンジ位置（Stochastic %K, fast, 既定 `inpPeriodOscillator=70`）を別ウィンドウ
の**オシレーター線**（DRAW_LINE, DarkGreen, width2）で可視化し、その系列全体（warm-up 0 込み）
の `平均 ±1.00σ / ±1.96σ`（P1/P2/M1/M2）を水平水準線として引く MQL4 インジケーターの
Python 移植。元 `sample/MQL4/Indicators/PRO!fitSTC.mq4`（実体 PRO!fitOscillator,
Copyright 2015, PRO!fit Investars, version 1.00）準拠。移植方針は `indigators/PORTING_GUIDE.md`
に従う。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `iStochastic`（%K, fast）/ `iBandsOnArray`（σ 帯）/ INDICATOR_MINIMUM/MAXIMUM |
| `src/stc.py` | 成果物層（DataFrame 整形） | `ExtBufferOscillator`（DRAW_LINE）書き込み / `StcLCStdDevArray[1..4]` 水準 |
| `src/loader.py` | 入力アダプタ（CSV → OHLC） | `CopyRates` / OnCalculate 引数の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window + DRAW_LINE(DarkGreen, width2) + σ 水準線 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_line` / `horizontal_line`） |
| `tests/test_core.py` | core 計算の検証 | — |
| `tests/test_stc.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake） | — |
| `demo.py` | デモ（合成 OHLC → PNG） | — |

> `src/plot.py` は matplotlib 依存のため、先例（profit_adx_needle）同様 `src/__init__.py`
> の公開 API から除外する（matplotlib 未導入環境でも `import src` を壊さないため）。
> PNG 描画は `from src.plot import plot_stc` で明示的に import する。

## 使い方

```python
from src import load_ohlc_csv, build_stc, stc_levels

df = load_ohlc_csv("ohlc.csv")        # open/high/low/close 必須（列名の大小不問）
out = build_stc(df, period=70)        # stc_osc 1 列（warm-up は 0）
levels = stc_levels(df, period=70)    # P1/P2/M1/M2 + sub_min(=M2)/sub_max(=P2)
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_stc
plot_stc(df, "out.png", period=70)    # 下段にオシレーター線＋σ 水準線4本
```

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_line` / `horizontal_line` を
持つオブジェクトを duck typing で受ける）:
```python
from src.lwc_chart import add_stc
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_stc(sub, df, period=70)           # ライン1本（name=stc_osc）＋水準線4本
```

### デモ / テスト
```bash
python demo.py                 # profit_stc_demo.png を生成
python -m pytest -q            # 31 tests
```

## 計算の要点

1. **オシレーター（%K, fast）**: 直近 `period` 本の高安レンジに対する終値位置
   `%K = 100·(close-LL)/(HH-LL)`。元 `iStochastic(...,1,1,MODE_EMA,...,MODE_MAIN)` は
   slowing=1 / Dperiod=1 のため EMA 平滑が恒等（vestigial）で、生 %K に一致する。
2. **σ 水準（P1/P2/M1/M2）**: オシレーター系列全体（warm-up の 0 込み）の
   `平均 ±{1.00,1.96}×母標準偏差`。`iBandsOnArray(...,rates_total,...)` 相当。
3. **subwindow y 範囲**: `sub_min=M2`（INDICATOR_MINIMUM）〜 `sub_max=P2`（INDICATOR_MAXIMUM）。

## 元 MQL からの主な差分

- **warm-up は 0**: `i<period-1` の区間は元 iStochastic 既定どおり 0（NaN ではない）。
- **ゼロ割は 0**: 高安レンジ `HH==LL` のとき %K=0。
- **統計に warm-up の 0 が混入する**: σ 帯（平均・母標準偏差）は warm-up の 0 を除外せず
  全系列で算出する。これらはいずれも**原挙動の 1:1 再現**であり、"改善"しない（SPEC §9）。
- **Stochastic %K・σ 帯は本パッケージ内（in-package）に実装**: 将来 `common/` へシグネチャ
  不変で昇格する余地はあるが、現時点では本指標専用とする（YAGNI）。
- **MT4 純正との bit-exact は非保証**: 参照 CSV が無いため厳密一致は保証しない。

詳細仕様は `SPEC.md` を参照。
