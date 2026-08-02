# PRO!fitRSI（Python 移植）

適用価格（既定 Typical price）の相対力指数（Relative Strength Index, RSI, 既定
`InpRSIPeriod=6`）を別ウィンドウ（`[0,100]`）の**RSI 線**（DRAW_LINE, clrLime）で
可視化し、**生 RSI 系列**全体
（warm-up 0 込み）の `平均 ±1/2/3σ`（p1/p2/p3, m1/m2/m3）と中央線 `50`（mid50）を
水準線として引く MQL4 インジケーターの Python 移植。元 `sample/MQL4/Indicators/
PRO!fitRSI.mq4`（Copyright 2015, PRO!fit Investars, "Relative Strength Index"）準拠。
移植方針は `indigators/PORTING_GUIDE.md` に従う。元の EMA 平滑線（`ExtMABuffer` /
`InpMAPeriod`）は設定項目 `ma_period` ごと削除済み（承認 2026-08-02。SPEC §2）。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `iRSI`（権威 Wilder, RSI 本線）/ `iStdDevOnArray`＋`iMAOnArray(MODE_SMA)`（生 RSI に σ 7 水準）/ Apply→適用価格 写像 / indicator_minimum/maximum |
| `src/rsi.py` | 成果物層（DataFrame 整形） | `ExtRSIBuffer`（DRAW_LINE）書き込み / StDevA1..A6 ＋ 50 水準 |
| `src/loader.py` | 入力アダプタ（CSV → OHLC） | `OnCalculate` 引数 open/high/low/close[] の供給（**volume 不要**） |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window [0,100] + DRAW_LINE×1(clrLime) + σ 水準線 7 本 + 短名（Apply 依存） |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_line`×1 / `horizontal_line`×7） |
| `tests/test_core.py` | core 計算の検証 | — |
| `tests/test_rsi.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake） | — |
| `demo.py` | デモ（合成 OHLC → PNG） | — |

> `src/plot.py` は matplotlib 依存のため、先例（profit_mfi/profit_stc）同様
> `src/__init__.py` の公開 API から除外する（matplotlib 未導入環境でも `import src`
> を壊さないため）。PNG 描画は `from src.plot import plot_rsi` で明示的に import する。

## 使い方

```python
from src import load_ohlc_csv, build_rsi, rsi_levels

df = load_ohlc_csv("ohlc.csv")              # open/high/low/close 必須（大小不問・volume 不要）
out = build_rsi(df, rsi_period=6, apply=5)   # rsi 1 列（warm-up は 0）
levels = rsi_levels(df, rsi_period=6, apply=5)  # p1/p2/p3/m1/m2/m3/mid50(=50)
```

`apply` は適用価格（1=Open .. 6=Weighted close, 既定 5=Typical, 既定外=Close）。

### 描画（matplotlib / PNG）
```python
from src.plot import plot_rsi
plot_rsi(df, "out.png", rsi_period=6, apply=5)  # 下段に RSI 線＋σ 水準線7本
```

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_line` / `horizontal_line` を
持つオブジェクトを duck typing で受ける。`lightweight_charts` は import しない）:
```python
from src.lwc_chart import add_rsi
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_rsi(sub, df, rsi_period=6, apply=5)  # ライン1本(rsi)＋水準線7本
```

### デモ / テスト
```bash
python demo.py                 # profit_rsi_demo.png を生成
python -m pytest -q            # 43 tests
```

## 計算の要点

1. **iRSI（権威 Wilder）**: `diff=price[i]-price[i-1]`。seed=直近 `rsi_period` 本の
   up/down 平均、main=Wilder 平滑 `(prev*(period-1)+x)/period`。ゼロ割は
   neg0&pos≠0→100 / **neg0&pos0（flat window）→50** / それ以外→`100-100/(1+pos/neg)`。
   warm-up は 0。適用価格は共有 `common`（Apply→PRICE_*）。
2. **σ 7 水準**: **生 RSI 系列**全体（warm-up の 0 込み）の `平均 ±{1,2,3}×母標準偏差`
   ＋ 中央線 50。`iStdDevOnArray(ExtRSIBuffer)` / `iMAOnArray(ExtRSIBuffer, MODE_SMA)`
   相当（**平滑系列ではなく生 RSI に掛ける**）。
3. **subwindow y 範囲**: `[0,100]`（元 indicator_minimum 0 / indicator_maximum 100）。
4. **短名**: "RSI-{適用価格名} ({period})"（Apply 依存。例 Apply=5 → "RSI-Typical price (6)"）。

## 元 MQL からの主な差分

- **warm-up は 0**: `i<rsi_period` の区間は元 iRSI 既定どおり 0（NaN ではない）。
- **ゼロ割の場合分け**: neg0&pos≠0→100 / **neg0&pos0→50（flat window。MFI の 100 と
  異なる）** / それ以外→`100-100/(1+pos/neg)`。
- **σ 統計は生 RSI 系列に掛ける**: 元コードの `iStdDevOnArray`/`iMAOnArray` 引数が
  `ExtRSIBuffer`（平滑後の ExtMABuffer ではない）。**warm-up の 0 も除外せず全系列で算出**。
- **EMA 平滑線（`ExtMABuffer` / `InpMAPeriod`）は移植しない**（承認 2026-08-02）。
  σ 水準は元から生 RSI 由来のため、水準値・RSI 線の値は削除前と一致する。
- これらはいずれも**原挙動の 1:1 再現**であり "改善" しない（SPEC §9）。
- **iRSI は権威 Wilder（MetaQuotes 公式 `RSI.mq5`）準拠**。**適用価格は共有層を
  再利用**（重複排除）。**σ 統計は in-package**（iRSI は共有 `mql_builtins`）。
- **bit-exact 非保証**: 元 `iRSI` は MT4 組込であり、参照 CSV が無いため厳密一致は保証しない。

詳細仕様は `SPEC.md` を参照。
