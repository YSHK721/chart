# !!R-tgp.BTLM-Ind (Python 移植)

MQL4 インジケーター `!!R-tgp.BTLM-Ind.mq4` の Python 移植。元コードは計算を R の
`tgp` パッケージ（`btlm` = Bayesian Treed Linear Model）へ委譲し、直近 `maxbars` 本の
**Open 価格をバー番号に回帰**して、予測平均と上下分位点（信用区間）を 3 本のラインで描く。

`.doc/PORTING_GUIDE.md` に準拠し、計算（core/bands）と入出力アダプタ（loader/plot/
lwc_chart）を分離。R 連携（rpy2）は `BtlmFitter` ポートの外側（`rbridge.py`）へ隔離する。

## 構成

| ファイル | 層 / 責務 |
|---|---|
| `src/core.py` | 純粋計算（numpy のみ）: DTO・ポート・設計補助・逆正規分位 |
| `src/bands.py` | 成果物層: 予測結果 → DataFrame（窓外 NaN） |
| `src/rbridge.py` | R tgp バックエンド（rpy2 遅延 import・偶有的依存を隔離） |
| `src/reference.py` | numpy 参照バックエンド（R 不要・デモ/テスト/フォールバック） |
| `src/loader.py` | 入力アダプタ（CSV → OHLC DataFrame） |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts / Line・duck typing） |
| `tests/` | 計算検証 + Fake チャート検証 |
| `demo.py` | matplotlib デモ（PNG 生成） |
| `lwc_demo.py` | lightweight-charts デモ（HTML / スクリーンショット生成） |
| `SPEC.md` | 移植仕様書 |

## 依存

- Python 3.10+
- numpy, pandas（実行時）/ matplotlib（描画）/ pytest（開発時）
- **忠実バックエンドのみ**: R 本体 + R パッケージ `tgp` + `rpy2`（`TgpBtlmFitter` 利用時）

## 使い方

```python
from src import load_ohlc_csv, build_btlm_bands, OlsBtlmFitter, TgpBtlmFitter

df = load_ohlc_csv("ohlc.csv", time_column="time")  # open 列が必要

# R 不要の参照実装（区分なし線形）。
bands = build_btlm_bands(df, OlsBtlmFitter(), maxbars=100, q_low=0.05, q_high=0.95)

# 忠実な btlm（要 R + tgp + rpy2）。
bands = build_btlm_bands(df, TgpBtlmFitter(seed=1), maxbars=100)

print(bands.tail()[["btlm_mean", "btlm_q5", "btlm_q95"]])
```

描画（matplotlib / PNG）:

```python
from src.plot import plot_btlm
plot_btlm(df, OlsBtlmFitter(), out_path="out.png", maxbars=100)
```

### インディケーターとして使う（lightweight-charts）

`add_btlm(chart, df, fitter)` が `build_btlm_bands` を計算し、チャートへ 3 ライン
（`btlm_mean`=実線、`btlm_q{lo}`/`btlm_q{hi}`=点線、MediumSlateBlue）を重畳する。
`chart` は `create_line` を備えたオブジェクト（lightweight_charts の `AbstractChart` 系）
であればよく、本モジュールは `lightweight_charts` を import しない（依存は numpy/pandas）。

```python
from lightweight_charts import Chart
from src import OlsBtlmFitter
from src.lwc_chart import add_btlm

chart = Chart()
chart.set(df)                                       # ローソク足（open/high/low/close + time/date）
add_btlm(chart, df, OlsBtlmFitter(), maxbars=250)   # 直近 250 本に回帰チャネルを重畳
chart.show(block=True)
```

3 ラインは当てはめ窓（直近 `maxbars` 本）にのみ描かれ、窓外は NaN として自動除外される。
**制約**: ラッパーの公開 API は `create_line` のみで 2 線間の塗り(fill)は非対応のため、
信用区間は上下の点線で表現する（塗りは不可）。

デモ実行（ヘッドレスは Xvfb と有効ロケールが必要）:

```bash
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
WEBKIT_DISABLE_COMPOSITING_MODE=1 WEBKIT_DISABLE_DMABUF_RENDERER=1 \
LIBGL_ALWAYS_SOFTWARE=1 WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 \
xvfb-run -a python lwc_demo.py        # out/ に HTML と PNG を出力
```

## 出力列

`btlm_mean` / `btlm_q{lo}` / `btlm_q{hi}`（既定 `btlm_q5` / `btlm_q95`）。
当てはめ窓（直近 `min(maxbars, 行数)` 本）以外の行は `NaN`（元 `EMPTY_VALUE` 相当）。

## バックエンド選択

| | TgpBtlmFitter | OlsBtlmFitter |
|---|---|---|
| 中身 | R `tgp::btlm`（ベイズ木構造線形・MCMC） | numpy 単一区分ベイズ線形回帰 |
| 忠実度 | 元と一致（シード固定・確率的） | 概念近似（木分割なし） |
| 依存 | R + tgp + rpy2 | numpy のみ |
| 用途 | 本番・忠実再現 | デモ・テスト・R 不在環境 |

## 計算式

### 共通: 当てはめ窓と設計

直近 $w = \min(\text{maxbars}, N)$ 本（$N$=行数）を窓とし、説明変数と目的変数を組む。

$$
x_i = i \quad (i = 1, 2, \dots, w), \qquad z_i = \text{open}_{N-w+i}
$$

出力は窓内のみ。窓外（先頭 $N-w$ 行）は $\text{NaN}$（元 `EMPTY_VALUE`）。

### TgpBtlmFitter（忠実: R `tgp::btlm`）

R のベイズ木構造線形モデルを `pred.n=TRUE` で当てはめ、学習点 $x_i$ における予測分布の
統計量を取得する。

$$
\text{mean}_i = \texttt{model\$Zp.mean}_i,\quad
\hat{q}^{\,5}_i = \texttt{model\$Zp.q1}_i,\quad
\hat{q}^{\,95}_i = \texttt{model\$Zp.q2}_i
$$

btlm はパラメータ $\theta$（木構造・区分ごとの線形係数・分散）の事後分布を MCMC
（`BTE=`$(B,T,E)=(2000,15000,2)$: バーンイン $B$、総反復 $T$、間引き $E$、リスタート $R=1$）で
サンプリングし、各点の予測分布 $p(z \mid x_i, \text{data})$ から平均・5%・95% 分位点を得る。

**分位点の指定（`q_low`, `q_high`）**: 既定 $(0.05, 0.95)$ はネイティブ値をそのまま使う。
非既定の場合はネイティブ 90% 帯を正規近似し、標準偏差を逆算して再構成する。

$$
\sigma_i = \frac{\hat{q}^{\,95}_i - \hat{q}^{\,5}_i}{2\,z_{0.95}}, \qquad z_{0.95}=\Phi^{-1}(0.95)\approx 1.6449
$$

$$
\text{lower}_i = \text{mean}_i + \Phi^{-1}(q_{\text{low}})\,\sigma_i, \qquad
\text{upper}_i = \text{mean}_i + \Phi^{-1}(q_{\text{high}})\,\sigma_i
$$

ここで $\Phi^{-1}$ は標準正規の逆累積分布関数（`core.norm_ppf`、Acklam の有理近似、相対誤差約 $1.15\times10^{-9}$）。

### OlsBtlmFitter（参照: numpy 単一区分ベイズ線形回帰）

btlm から木分割を除いた単一区分（＝通常の線形回帰）に退化させた近似。設計行列を
$\Phi = [\,\mathbf{1}\;\; \mathbf{x}\,] \in \mathbb{R}^{w\times 2}$ とする。

$$
\hat{\beta} = (\Phi^{\top}\Phi)^{-1}\Phi^{\top} z, \qquad
\text{mean} = \Phi\hat{\beta}
$$

残差分散（自由度 $w-2$）と各点のレバレッジから予測標準偏差を求める。

$$
s^2 = \frac{\lVert z - \text{mean} \rVert^2}{w-2}, \qquad
h_i = \phi_i^{\top}(\Phi^{\top}\Phi)^{-1}\phi_i, \qquad
\text{sd}_i = \sqrt{s^2\,(1 + h_i)}
$$

$$
\text{lower}_i = \text{mean}_i + \Phi^{-1}(q_{\text{low}})\,\text{sd}_i, \qquad
\text{upper}_i = \text{mean}_i + \Phi^{-1}(q_{\text{high}})\,\text{sd}_i
$$

$1 + h_i$ の $1$ は観測ノイズ（新規予測の分散）、$h_i$ は推定平均自体の不確実性に対応する。

> 注: OlsBtlmFitter は概念近似であり木分割を持たない（平均は直線）。非線形・レジーム変化を
> 表すには `TgpBtlmFitter` を用いる。

## 元 MQL4 からの変更点

- **計算の R 委譲を維持しつつ隔離**: 元は `mt4R` ブリッジで R を呼ぶ。本移植は rpy2 で
  `tgp::btlm` を呼ぶ `TgpBtlmFitter` を用意し、ポート境界の外へ閉じ込めた。
- **非同期 R 連携（RExecuteAsync / RIsBusy）は除去**: MT4 のティック駆動を待たないための
  足場であり、バッチ移植では同期実行。計算結果は不変。
- **時系列の向き**: 元は series 順（index0=最新）を `rev` で昇順化して R へ渡す。Python は
  昇順で扱うため `rev` 不要（ガイド §4.3）。
- **分位点を可変化**: 元は tgp 既定の `Zp.q1=5%` / `Zp.q2=95%`。本移植は `q_low/q_high` 引数で
  指定可能（既定は 5/95%）。非既定値は `TgpBtlmFitter` ではネイティブ 90% 帯からの
  正規近似で再構成する（README/SPEC に明記）。
- **`int()` 切り捨て**: 該当なし（元コードに値幅の整数化は無い）。

## テスト

```bash
cd indicators/tgp_btlm
python -m pytest tests/ -q
```
