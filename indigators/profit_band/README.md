# PRO!fit_Band (Python 再設計)

MQL5 インジケーター `PRO!fit_Band.mq5` の計算部を Python の計算ライブラリとして
再設計したもの。ローソク足を陽線/陰線/同値に分類し、始値からの値幅の分位点（51〜99%）
を求めて、始値基準の統計バンドを生成する。

## 構成

| ファイル | 責務 |
|---|---|
| `src/core.py` | 値幅サンプル収集・分位点計算（純粋ロジック・I/O非依存） |
| `src/bands.py` | 分位点から始値基準バンド DataFrame を生成 |
| `src/loader.py` | CSV → OHLC DataFrame の入力アダプタ |
| `src/plot.py` | matplotlib によるバンド描画（PNG 出力） |
| `src/lwc_chart.py` | lightweight-charts へのバンド描画アダプタ（Line 系列として重畳） |
| `lwc_demo.py` | lightweight-charts デモ（HTML / スクリーンショット生成） |
| `tests/` | pytest による振る舞い検証 |

## 依存

- Python 3.10+
- numpy, pandas（実行時）/ pytest（開発時）

## 使い方

```python
from src import load_ohlc_csv, build_bands

df = load_ohlc_csv("ohlc.csv", time_column="time")  # open/high/low/close 列が必要
bands = build_bands(df)        # 28 列（pOL/nOH/pOH/nOL × 51..99%）の DataFrame
print(bands[["nOH_99", "pOL_99"]])
```

CSV は `open,high,low,close`（列名の大小文字は不問）を含む形式。任意で時刻列を指定可。

### インディケーターとして使う（lightweight-charts）

`add_profit_band(chart, df)` が `build_bands` を計算し、チャートへ Line として重畳する。
`chart` は `create_line` / `legend` を備えたオブジェクト（lightweight_charts の
`AbstractChart` 系）であればよく、本モジュールは `lightweight_charts` を import しない
（profit_band の依存は numpy/pandas のまま）。

```python
from lightweight_charts import Chart
from src.lwc_chart import add_profit_band

chart = Chart()
chart.set(df)                    # ローソク足（open/high/low/close + time/date）
add_profit_band(chart, df)       # バンド 28 本（既定: nOH/pOL 実線, pOH/nOL 点線, 51〜99%）
chart.show(block=True)

# 絞り込み例: 95% の主バンド端のみ
add_profit_band(chart, df, buckets=("nOH", "pOL"), probabilities=(0.95,))
```

**制約**: ラッパーの公開 API は `create_line` のみで 2 線間の塗り(fill)は非対応のため、
塗りバンドは上端(`pOL`)/下端(`nOH`)の実線、外側(`pOH`/`nOL`)の点線で表現する。
パーセンタイルが外側ほど不透明度を下げる。

デモ実行（ヘッドレスは Xvfb と有効ロケールが必要）:

```bash
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
WEBKIT_DISABLE_COMPOSITING_MODE=1 WEBKIT_DISABLE_DMABUF_RENDERER=1 \
LIBGL_ALWAYS_SOFTWARE=1 WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 \
xvfb-run -a python lwc_demo.py        # out/ に HTML と PNG を出力
```

## 頑健版バンド（build_robust_bands）

既定の `build_bands` は **絶対価格で・全期間（未来含む）から** 帯を作るため、価格水準が
変動する系列ではスケール非不変・先読み・名目確率の不成立という統計的欠陥がある
（実証は `analysis/EVALUATION.md`）。これを是正した代替計算が `build_robust_bands`。
**既存 `build_bands` は変更していない。**

- **スケール不変化**: `normalize="return"`（値幅/始値）または `"atr"`（値幅/ATR）。
- **先読み除去（因果窓）**: `window="expanding"`（その足までの全過去）または `window=<int>`
  （直近 N 本の rolling）。初期 `min_obs` 未満の足は NaN。

```python
from src import build_robust_bands
from src.lwc_chart import add_robust_profit_band

bands = build_robust_bands(df, normalize="return", window="expanding")  # 時変・初期NaN

# チャートへ（正規化＋因果窓）
add_robust_profit_band(chart, df, probabilities=(0.95,),
                       normalize="return", window="expanding")
```

実データ（close 1.05→407）での是正効果：負価格 9908→**0**、帯幅率 386倍→**1.67倍**。

## 計算方法

入力 OHLC 全体から「始値からの値幅」の統計（分位点）を求め、それを各足の始値に
加減してバンドを生成する。計算は 4 段階。

### 1. 値幅の算出（足ごと）

各足について 3 種の値幅を絶対値で求める。

- `OH = |open − high|`
- `OL = |open − low|`
- `HL = |high − low|`

元 MQL5 は値幅を `int(...)` で整数化していたが、本実装は **float 精度**で計算する
（小数価格で 0 になる不具合の回避）。

### 2. 足の分類とバケット集計

各足を陽線 / 陰線 / 同値に分類し、値幅を 6 バケット（`pOH/pOL/pHL/nOH/nOL/nHL`）へ
振り分ける（`p`=陽線側 / `n`=陰線側、`OH/OL/HL`=値幅種別）。同値足は非対称に配分する
（元 MQL5 ロジックを忠実再現）。

| 分類 | 条件 | OH の行き先 | OL の行き先 | HL の行き先 |
|---|---|---|---|---|
| 陽線 | `open < close` | pOH | pOL | pHL |
| 陰線 | `open > close` | nOH | nOL | nHL |
| 同値 | `open == close` | pOH | nOL | pHL と nHL |

結果として各バケットのサンプル集合は次のとおり。

- `pOH` = OH(陽線) + OH(同値) / `pOL` = OL(陽線) / `pHL` = HL(陽線) + HL(同値)
- `nOH` = OH(陰線) / `nOL` = OL(陰線) + OL(同値) / `nHL` = HL(陰線) + HL(同値)

### 3. 分位点（パーセンタイル）

各バケットのサンプル集合に対し、確率 `0.51 / 0.80 / 0.85 / 0.90 / 0.95 / 0.98 / 0.99` の
分位点を `np.quantile(method="linear")`（線形補間 = R type-7、MQL5 `MathQuantile` と同一）で
算出する。空バケットは `NaN`。**この分位点はデータ全体から 1 組だけ求まる大域統計**で、
全足に共通のオフセットとして使われる。

### 4. 始値基準バンドの生成

描画対象は `pOL / nOH / pOH / nOL` の 4 系統。各足 `i` の始値に分位点 `q` を加減する
（上側は加算、下側は減算）。

- 上側: `pOL_p[i] = open[i] + q_pOL(p)`、`pOH_p[i] = open[i] + q_pOH(p)`
- 下側: `nOH_p[i] = open[i] − q_nOH(p)`、`nOL_p[i] = open[i] − q_nOL(p)`

4 系統 × 7 確率 = **28 列**。各列は「始値 ± 一定オフセット」のため、バンドは始値に
密着して上下へ広がる。`pHL` / `nHL` は集計されるが描画バンドには未使用（元ロジック準拠）。

## 出力列

`{bucket}_{percent}` 形式。

- `pOL_*` / `pOH_*`: 始値の上側（始値 + 分位点）
- `nOH_*` / `nOL_*`: 始値の下側（始値 − 分位点）

塗りバンドは `nOH`(下)〜`pOL`(上)、外側点線は `nOL`(下)/`pOH`(上) に対応（元 MT5 描画）。

## 元 MQL5 からの変更点

- **int() 切り捨ての廃止**: 元コードは値幅を `int(open-high)` 等で整数化しており、
  FX 等の小数価格では結果が 0 になる不具合があった。本実装は float 精度で計算する。
- **分位点方式**: MQL5 `MathQuantile`（線形補間 = numpy 既定 `linear` / R type-7）と一致。
- **同値足の扱い**: 元ロジックを忠実再現（pOH/nOL/pHL/nHL に加算する非対称分類）。
- 描画（MT5 バッファ/プロット）は本ライブラリの範囲外。バンド値の算出のみを担う。

## テスト

```bash
cd indicators/profit_band
python -m pytest tests/ -q
```
