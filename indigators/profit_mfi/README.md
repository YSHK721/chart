# PRO!fitMFI（Python 移植）

出来高加重の資金流入出（Money Flow Index, MFI, 既定 `InpMFIPeriod=14`）を別ウィンドウ
（`[0,100]`）の**MFI 線**（DRAW_LINE, clrLime）と**EMA 平滑線**（`InpMAPeriod=5`,
DRAW_LINE, clrLime）で可視化し、平滑系列全体（warm-up 0 込み）の `平均 ±1/2/3σ`
（p1/p2/p3, m1/m2/m3）と中央線 `50`（mid50）を水準線として引く MQL4 インジケーターの
Python 移植。元 `sample/MQL4/Indicators/PRO!fitMFI.mq4`（Copyright 2015, PRO!fit
Investars, "Money Flow Index"）準拠。移植方針は `indigators/PORTING_GUIDE.md` に従う。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `iMFI`（MFI 本線）/ `iMAOnArray(MODE_EMA)`（共有再利用）/ `iStdDevOnArray`＋`iMAOnArray(MODE_SMA)`（σ 7 水準）/ indicator_minimum/maximum |
| `src/mfi.py` | 成果物層（DataFrame 整形） | `ExtMFIBuffer` / `ExtMABuffer`（DRAW_LINE）書き込み / StDevA1..A6 ＋ 50 水準 |
| `src/loader.py` | 入力アダプタ（CSV → OHLCV） | `OnCalculate` 引数 high/low/close/**volume**[] の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window [0,100] + DRAW_LINE×2(clrLime) + σ 水準線 7 本 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_line`×2 / `horizontal_line`×7） |
| `tests/test_core.py` | core 計算の検証 | — |
| `tests/test_mfi.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake） | — |
| `demo.py` | デモ（合成 OHLCV → PNG） | — |

> `src/plot.py` は matplotlib 依存のため、先例（profit_stc）同様 `src/__init__.py` の
> 公開 API から除外する（matplotlib 未導入環境でも `import src` を壊さないため）。
> PNG 描画は `from src.plot import plot_mfi` で明示的に import する。

## 使い方

```python
from src import load_ohlcv_csv, build_mfi, mfi_levels

df = load_ohlcv_csv("ohlcv.csv")            # open/high/low/close/volume 必須（大小不問）
out = build_mfi(df, mfi_period=14, ma_period=5)   # mfi / mfi_ma 2 列（warm-up は 0）
levels = mfi_levels(df, mfi_period=14, ma_period=5)  # p1/p2/p3/m1/m2/m3/mid50(=50)
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_mfi
plot_mfi(df, "out.png", mfi_period=14, ma_period=5)  # 下段に MFI/EMA 線＋σ 水準線7本
```

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_line` / `horizontal_line` を
持つオブジェクトを duck typing で受ける。`lightweight_charts` は import しない）:
```python
from src.lwc_chart import add_mfi
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_mfi(sub, df, mfi_period=14, ma_period=5)  # ライン2本(mfi/mfi_ma)＋水準線7本
```

### デモ / テスト
```bash
python demo.py                 # profit_mfi_demo.png を生成
python -m pytest -q            # 26 tests
```

## 計算の要点

1. **iMFI**: `TP=(H+L+C)/3`, `MF=TP×Volume`。直近 `mfi_period` 本の窓で
   `TP[j]>TP[j-1]→正MF` / `TP[j]<TP[j-1]→負MF`（**同値は非加算・非対称**）。
   `MFI=100×正MF/(正MF+負MF)`。ゼロ割は 負MF0→100 / 正MF0→0 / 両0→0。warm-up は 0。
2. **EMA 平滑**: MFI 系列（warm-up 0 込み）を**共有 `moving_averages` の EMA** で平滑化
   （in-package 再実装しない）。
3. **σ 7 水準**: EMA 平滑系列全体（warm-up の 0 込み）の `平均 ±{1,2,3}×母標準偏差`
   ＋ 中央線 50。`iStdDevOnArray` / `iMAOnArray(MODE_SMA)` 相当。
4. **subwindow y 範囲**: `[0,100]`（元 indicator_minimum 0 / indicator_maximum 100）。

## 元 MQL からの主な差分

- **warm-up は 0**: `i<mfi_period` の区間は元 iMFI 既定どおり 0（NaN ではない）。
- **ゼロ割の場合分け**: 負MF0→100 / 正MF0→0 / 両0→0。
- **同値非対称**: `TP[j]==TP[j-1]` は正負いずれにも加算しない（元挙動の 1:1 再現）。
- **統計に warm-up の 0 が混入する**: σ 7 水準（平均・母標準偏差）は warm-up の 0 を
  除外せず全系列で算出する。これらはいずれも**原挙動の 1:1 再現**であり "改善" しない（SPEC §9）。
- **EMA は共有 moving_averages を流用**（重複排除）。**iMFI と σ 統計は in-package**
  （iMFI は MFIMACD 着手時に共有昇格を判断、σ 統計はバッチ後 common 集約）。
- **volume の tick/実出来高 は CSV 列定義依存で bit-exact 非保証**。MT4 純正との厳密一致は
  参照 CSV が無いため保証しない。

詳細仕様は `SPEC.md` を参照。
