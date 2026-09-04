# 価格帯別ブルベアレシオ（PriceRangePower）

VBA インジケーター `TA.PriceRangePower`（`sample/VBA/TECNICAL_ANALYSIS.cls`、UserForm
`PriceRangePower.frm` から起動）の Python 移植。OHLC を価格帯（級）別に集計し、各バーの
始値→終値方向で分類したヒゲ幅の **±1/2/3σ 度数** とその比率（ブル/ベアレシオ）を算出する。
安値帯に集まる **支持（ブル）勢力** と高値帯に集まる **抵抗（ベア）勢力** を価格帯ごとに可視化する。

移植方針は `.doc/PORTING_GUIDE.md`（計算と入出力の分離・依存は内向き）に準拠。仕様の詳細は
[`SPEC.md`](./SPEC.md)。

## 構成

```
price_range_power/
├── README.md / SPEC.md
├── src/
│   ├── __init__.py     # 公開 API
│   ├── core.py         # 純粋計算（numpy のみ）：分類・ヒゲ幅・σ閾値・度数・比率
│   ├── ratio.py        # 成果物層：成果物 DataFrame / ブル・ベア勢力プロファイル
│   ├── loader.py       # 入力アダプタ：CSV → OHLC DataFrame
│   ├── plot.py         # 出力アダプタ：matplotlib 価格帯プロファイル PNG
│   └── lwc_chart.py    # 出力アダプタ：lightweight-charts 水平線（duck typing）
├── tests/
│   ├── test_core.py        # 計算（VBA 1:1 転記オラクルとの突合 + 手計算アンカー）
│   └── test_lwc_chart.py   # 出力アダプタ（Fake チャート）
├── demo.py             # 合成 OHLC → プロファイル PNG 生成（matplotlib）
└── lwc_demo.py         # lightweight-charts デモ（HTML / スクリーンショット生成）
```

| 概念 | 元 VBA | 移植先 |
|---|---|---|
| 方向分類・ヒゲ幅 | `fun_OpeCHANGE("OC")` + Select Case | `core.wick_samples` |
| σ閾値（平均±1/2/3σ） | `opeAverage` / `opeSTDEV` | `core.wick_stats`（該当バーのみ・標本σ） |
| 価格帯生成 | `RoundUp(prev+iv,4)` ループ | `core.build_price_bands` |
| 度数集計・比率 | resPRP 列 1..27 | `core.compute_price_range_power` |
| 表出力 | `pD.iDataWrite` + `DF_PricerangePower` | `ratio.build_price_range_power` / `plot` / `lwc_chart` |

## 使い方

```python
from src import load_ohlc_csv, build_price_range_power, build_bull_bear_profile

df = load_ohlc_csv("ohlc.csv")                 # open/high/low/close（大小不問・昇順）

# 成果物 DataFrame（価格帯 index・度数14 + 比率12 + total）。元 resPRP 相当。
res = build_price_range_power(df, interval=0.1)   # interval ∈ {0.1, 0.01, 0.001}

# 帯別ブル/ベア勢力の要約（描画・分析用）。
prof = build_bull_bear_profile(df, interval=0.1)
print(prof.sort_values("net_power").head())       # 抵抗（ベア）優位の価格帯
```

描画:

```python
from src.plot import plot_price_range_power
plot_price_range_power(df, "out.png", interval=0.1)        # 価格帯プロファイル

# lightweight-charts（chart は create/horizontal_line を持つオブジェクト）
from src.lwc_chart import add_price_range_power
add_price_range_power(chart, df, interval=0.1, top_n=5)    # 支持/抵抗帯を水平線で重畳
```

`range_from` / `range_to` を省略すると、元 VBA 同様に安値の最小値 / 高値の最大値が使われる。

## 成果物の列（元 resPRP 対応）

- 度数: `fda_f_l`/`fda_f_h`（価格帯出現度数）、`f_ol_a{1,2,3}`/`f_lh_a{1,2,3}`（ブル: 安値帯）、
  `f_hc_a{1,2,3}`/`f_hl_a{1,2,3}`（ベア: 高値帯）。
- 比率: 上記に `_pct` を付した 12 列（分母 = `fda_f_l` または `fda_f_h`）。**分母/分子いずれか 0 は NaN**。
- `total`: 比率 12 列の合計（NaN は 0 とみなす）。

## デモ

```bash
python demo.py        # price_range_power_demo.png を生成
```

![demo](./price_range_power_demo.png)

## テスト

```bash
python -m pytest -q   # 20 passed
```

計算は `tests/test_core.py` の独立リファレンス `_ref_prp`（VBA 擬似コードを非ベクトル化で 1:1
転記したオラクル）と、手計算可能な微小入力での明示アンカーで二重に固定している。

## 元 VBA からの主な変更点

- `WorksheetFunction.RoundUp` の浮動小数ノイズを 6 桁丸めで安定化（実装都合の桁あふれ除去）。
- 元 resPRP 末尾の空（級=0）行アーティファクトは再現せず、意味のある価格帯のみ生成。
- Excel シート I/O・表示書式・UserForm GUI は対象外（計算と描画の純粋化）。
- 統計は元 `opeAverage`/`opeSTDEV` の定義（該当バーのみ平均・標本標準偏差 ÷(n−1)）に忠実。

詳細・差分の網羅は [`SPEC.md`](./SPEC.md) §9 を参照。
