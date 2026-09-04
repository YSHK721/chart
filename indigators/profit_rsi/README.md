# PRO!fitRSI（Python 移植）

適用価格（既定 Typical price）の相対力指数（Relative Strength Index, RSI, 既定
`InpRSIPeriod=6`）を別ウィンドウ（`[0,100]`）の**RSI 線**（DRAW_LINE, clrLime）で
可視化し、その時点までの RSI 分布から **正常帯**（当該バー除外の因果ローリング分位）と
**外れ値水準**（POT＋経験的分位 / GPD 外挿・上下各 2 本）を重ねる MQL4 インジケーターの
Python 移植。元 `sample/MQL4/Indicators/
PRO!fitRSI.mq4`（Copyright 2015, PRO!fit Investars, "Relative Strength Index"）準拠。
移植方針は `indigators/PORTING_GUIDE.md` に従う。元の EMA 平滑線（`ExtMABuffer` /
`InpMAPeriod`）は設定項目 `ma_period` ごと削除し、元の σ 7 水準（全系列 avg±1/2/3σ＋固定 50）は
**非因果**（未来のバーを含む）のため因果ローリング分位＋POT/GPD へ全面置換した
（いずれも承認 2026-08-02。SPEC §2 / §5.4）。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy のみ） | `iRSI`（権威 Wilder, RSI 本線）/ Apply→適用価格 写像 / indicator_minimum/maximum |
| `src/levels.py` | 水準層（正常帯・POT/GPD） | （置換）StDevA1..A6 ＋ 50 水準 → 因果ローリング分位＋外れ値水準 |
| `src/rsi.py` | 成果物層（DataFrame 整形） | `ExtRSIBuffer`（DRAW_LINE）書き込み ＋ 水準列 |
| `src/loader.py` | 入力アダプタ（CSV → OHLC） | `OnCalculate` 引数 open/high/low/close[] の供給（**volume 不要**） |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window [0,100] + DRAW_LINE×1(clrLime) + 水準線 6 本 + 短名（Apply 依存） |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_line`×7・水平線は使わない） |
| `tests/test_core.py` | core 計算の検証 | — |
| `tests/test_levels.py` | 水準層の検証 | — |
| `tests/test_rsi.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake） | — |
| `demo.py` | デモ（合成 OHLC → PNG） | — |

> `src/plot.py` は matplotlib 依存のため、先例（profit_mfi/profit_stc）同様
> `src/__init__.py` の公開 API から除外する（matplotlib 未導入環境でも `import src`
> を壊さないため）。PNG 描画は `from src.plot import plot_rsi` で明示的に import する。

## 使い方

```python
from src import load_ohlc_csv, build_rsi

df = load_ohlc_csv("ohlc.csv")              # open/high/low/close 必須（大小不問・volume 不要）
out = build_rsi(df, rsi_period=6, apply=5)   # rsi ＋ 正常帯 2 列 ＋ 外れ値水準 4 列
# 列: rsi / rsi_q10 / rsi_q90 / rsi_evq_ext_hi / rsi_evq_ext_lo / rsi_gpd_hi / rsi_gpd_lo
```

水準パラメータ（既定）: `window_n=500`（正常帯の因果窓）・`q_low=0.10` / `q_high=0.90`
（正常帯＝POT 閾値）・`q_out=0.99`（外れ値の極端分位）・`k_events=50`（直近イベント件数）。
いずれも tickvol と同名・同既定。

`apply` は適用価格（1=Open .. 6=Weighted close, 既定 5=Typical, 既定外=Close）。

### 描画（matplotlib / PNG）
```python
from src.plot import plot_rsi
plot_rsi(df, "out.png", rsi_period=6, apply=5)  # 下段に RSI 線＋正常帯 2 本＋外れ値水準 4 本
```

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_line` / `horizontal_line` を
持つオブジェクトを duck typing で受ける。`lightweight_charts` は import しない）:
```python
from src.lwc_chart import add_rsi
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_rsi(sub, df, rsi_period=6, apply=5)  # ライン 7 本（rsi＋正常帯 2＋外れ値水準 4）
```

### デモ / テスト
```bash
python demo.py                 # profit_rsi_demo.png を生成
python -m pytest -q            # 54 tests
```

## 計算の要点

1. **iRSI（権威 Wilder）**: `diff=price[i]-price[i-1]`。seed=直近 `rsi_period` 本の
   up/down 平均、main=Wilder 平滑 `(prev*(period-1)+x)/period`。ゼロ割は
   neg0&pos≠0→100 / **neg0&pos0（flat window）→50** / それ以外→`100-100/(1+pos/neg)`。
   warm-up は 0。適用価格は共有 `common`（Apply→PRICE_*）。
2. **正常帯と外れ値水準**（元の σ 水準からの置換）: 当該バー除外の因果ローリング分位を
   閾値（POT の u）とし、超過を**余地割合** `(RSI−u)/(100−u)` で測ってエピソードへ畳み、
   直近 `k_events` 件から経験的分位と GPD 外挿の 2 通りで水準を出す。余地割合で測るため
   水準は構成上 [0,100] を出ない（生スケールでは実測 26〜35% が範囲外だった）。
   詳細と実測は SPEC §5.4。
3. **subwindow y 範囲**: `[0,100]`（元 indicator_minimum 0 / indicator_maximum 100）。
4. **短名**: "RSI-{適用価格名} ({period})"（Apply 依存。例 Apply=5 → "RSI-Typical price (6)"）。

## 元 MQL からの主な差分

- **warm-up は 0**: `i<rsi_period` の区間は元 iRSI 既定どおり 0（NaN ではない）。
- **ゼロ割の場合分け**: neg0&pos≠0→100 / **neg0&pos0→50（flat window。MFI の 100 と
  異なる）** / それ以外→`100-100/(1+pos/neg)`。
- **σ 7 水準（`iStdDevOnArray` / `iMAOnArray(MODE_SMA)`）は移植しない**（承認 2026-08-02）。
  元は**全系列**の統計＝バー t の水準が t より後のバーに依存する（非因果・リペイント）。
  因果ローリング分位＋POT/GPD へ全面置換した（SPEC §5.4）。RSI 本線の値は不変。
- **EMA 平滑線（`ExtMABuffer` / `InpMAPeriod`）も移植しない**（同承認）。
- これらはいずれも**原挙動の 1:1 再現**であり "改善" しない（SPEC §9）。
- **iRSI は権威 Wilder（MetaQuotes 公式 `RSI.mq5`）準拠**。**適用価格・分位バンド・
  イベント分位・GPD はすべて共有層を無改変で再利用**（重複排除）。in-package なのは
  「余地割合で測る」という本指標固有の構成だけ（`src/levels.py`）。
- **bit-exact 非保証**: 元 `iRSI` は MT4 組込であり、参照 CSV が無いため厳密一致は保証しない。

詳細仕様は `SPEC.md` を参照。
