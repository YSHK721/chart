# PRO!fit_HLBand 移植仕様書

## 1. Objective（目的）
High-Close / Low-Close の乖離（既定は**比率** `|H-C|/C` / `|L-C|/C`、後方互換モードは絶対距離
`|H-C|` / `|L-C|`）を**直近 W 本の因果窓**で集約し、その `平均` と `母標準偏差帯`
（dev = 0.67 / 1.65 / 1.96 / 2.58）を算出して起点終値 `close_ref = close[-2]`（元 `iClose(...,1)`）へ
投影した overlay バンド 8 本を、メインチャートに重ねて描く。終値からの High/Low 乖離の統計的
到達目安（±σ）を、最新確定足の終値を起点に可視化する。本指標は overlay 専用（separate
ウィンドウ・ヒストグラムを持たない）。

**既定は比率正規化（`normalize=True`）＋因果窓（`window=120`）の拡張版**であり、価格水準依存と
look-ahead（全履歴統計）を是正する。旧 MQL 挙動（全系列・絶対距離・加減算投影）は**後方互換
モード**（`window=None, normalize=False`）として bit 一致で残す。有効本数が 2 未満なら帯算出
不能とし `available=False`・8 バンド全 NaN を返す。

## 2. Scope（範囲・対象外）
- 移植する: 計算（距離 `|H-C|`/`|L-C|` → 全系列平均・母σ帯 → 起点 close[-2] への 8 投影）/
  描画の**意味**（main chart overlay の水平バンド線 8 本）/ 入力（CSV → OHLC）。
- 対象外:
  - **MT4 描画オブジェクト（`ObjectCreate` / `ObjectDelete` の OBJ_TREND）のライフサイクル**。
    本移植は 8 本の投影値（up_*/dn_*）を算出し、移植先では水平線として再表現する
    （オブジェクトの名前付き生成・削除・毎ティック再生成は移さない。描画の**意味**のみ移植）。
  - ブローカー接続・チャートデータ供給（`OnCalculate` 引数 high/low/close[] の供給、
    iHigh/iLow/iClose による系列参照）、アラート、最適化入力、リアルタイム差分再計算
    （バッチ全件計算で代替。ガイド §3/§6）。
  - separate ウィンドウ・ヒストグラム・プロット用バッファ（**本指標は元来持たない**）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fit_HLBand.mq4`（Copyright 2017, PRO!fit Investars）。
  MQL4。`#property strict`。
- 種別: **`#property indicator_chart_window`（メインチャート重畳 = overlay）**、
  `indicator_buffers 1`、`indicator_height 100`、`indicator_color1 DarkGreen`、
  `indicator_width1 1`、`indicator_levelcolor C'84,84,84'`（グレー）、
  `indicator_levelstyle STYLE_SOLID`。
- バッファ/プロット: `SetIndexBuffer` は **すべてコメントアウト**（OnInit 内 L156-163）。
  プロット用描画バッファ・DRAW_HISTOGRAM は**無い**。計算用の通常配列
  `ResBufferDivisionOpenHigh` / `ResBufferDivisionOpenLow` のみを使用する。
- overlay 8 本: `ObjectCreate(..., OBJ_TREND, 0, Time[1], StdDevArray[k], Time[0],
  StdDevArray[k])` ×8（L238-246。Time[1]→Time[0] の実質水平トレンドライン）、
  `OBJPROP_COLOR LimeGreen`（L249-252）。
- **input パラメータ: `inpSymbol`（string, NULL）/ `inpTimeFrame`（int, 0）のみ**。
  これは `iHigh/iLow/iClose/iBandsOnArray` のシンボル・時間足参照引数であり、**計算 period
  ではない**（移植先では「現在系列をそのまま使う」に対応し、引数を設けない）。
- 時系列の向き: `ArraySetAsSeries` 明示なし。`iHigh(...,i)` / `iClose(...,i)` の shift=i は
  「i 本前」（series 方向）。`iClose(...,1)` = 1 本前 = 直近確定足の終値。本移植は**昇順
  （古→新）**で扱い、`iClose(...,i)` の系列を昇順インデックスに 1:1 変換する。
  `iClose(...,1)`（1 本前）= 昇順末尾から 2 番目 = **`close[-2]`**。距離系列の平均・母σは
  全系列のため向きに依らず不変。
- 使用する標準/ライブラリ関数:
  - `MathAbs(iHigh(i) - iClose(i))` / `MathAbs(iLow(i) - iClose(i))`（L205-206）… 距離系列。
  - `iBandsOnArray(arr, 0, length, dev, 0, 1, 0)`（L220-227）… 配列全長 SMA ± dev×**母**
    標準偏差（MODE_UPPER=1）。dev ∈ {0.67, 1.65, 1.96, 2.58}。
  - `iClose(inpSymbol, inpTimeFrame, 1)`（L220-227）… 起点終値 close_ref。

## 4. Input（入力）
- 必須列: `high` / `low` / `close`（列名の大小不問）。CSV ローダ `load_ohlc_csv` は
  `open/high/low/close` を必須とする（open は本計算には不使用だが OHLC 規約として要求）。
- 拡張パラメータ（`compute_hl_band` / `hl_band_levels` のキーワード引数）:
  - `window: int | None = 120`（`DEFAULT_WINDOW`）… 帯幅統計に用いる末尾 W 本（直近窓）。
    `int>=1` または `None`（全長＝後方互換）。`window<1`（0・負）は窓として無意味なため
    `ValueError`。`close_ref` は窓に依らず `close[-2]` を維持する。
  - `normalize: bool = True`… `True` で比率正規化（per-bar `|X-C|/C`・乗算投影）、`False` で
    絶対距離（加減算投影・後方互換）。
  - `比率モードは close > 0` を要求（0 除算ガード。`close<=0` を含むと `ValueError`）。
- 時刻列: 描画アダプタ（`add_hl_band`）は `time`/`date` 列または `DatetimeIndex` を時刻解決
  に用いる（overlay の水平線は価格軸スカラだが、元が時系列チャートへの重畳であること・先例の
  異常系整合のため時刻解決可能性を要求する）。
- 前提: 行は時系列昇順。最低 **2 行**（`close[-2]` 起点のため `N>=2`）。欠損なし（NaN 前提
  処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 per-bar 系列（`normalize` で分岐）
全バー `i`（warm-up なし・NaN なし）。
- `normalize=True`（既定・`compute_ratios`）— **比率**（価格水準依存の是正・スケール不変）:
```
r_high[i] = |high[i] - close[i]| / close[i]     # per-bar 正規化（各バー自身の close で除算）
r_low[i]  = |low[i]  - close[i]| / close[i]     # close[i] > 0 が必要（0 除算ガード）
```
- `normalize=False`（後方互換・`compute_distances`）— **絶対距離**:
```
dist_high[i] = |high[i] - close[i]|     # 元 MathAbs(iHigh(i) - iClose(i))（L205）
dist_low[i]  = |low[i]  - close[i]|     # 元 MathAbs(iLow(i)  - iClose(i))（L206）
```
以降、選択された系列を `series_high` / `series_low` と呼ぶ。

### 5.2 因果窓（直近 W 本・`_tail`）
帯幅統計には系列末尾 W 本のみを用いる（履歴長非依存・look-ahead 是正）:
```
slice = series[-window:]   （window=int>=1）   /   slice = series（window=None・全長）
```
`close_ref` はこのスライスに依らず別途 `close[-2]` を維持する（比率の分母 close[i] と投影
基準 close[-2] は別物である点に注意）。`window<1` は `ValueError`。

### 5.3 帯（`band_upper`, `iBandsOnArray` MODE_UPPER 相当）
スライスの算術平均 `mean` と**母**標準偏差 `sigma = sqrt(mean((x-mean)^2))`（÷N）から:
```
band_upper(slice, dev) = mean(slice) + dev * sigma(slice)
```
dev ∈ {0.67, 1.65, 1.96, 2.58}（`HL_BAND_DEVS`）。`normalize=True` では `band_upper` は
相対オフセット（無次元の比率量）、`False` では絶対距離量を返す。

### 5.4 起点終値（`close_ref`）
```
close_ref = close[-2]     # 元 iClose(inpSymbol, inpTimeFrame, 1) = 1 本前 = 昇順末尾-1
```

### 5.5 有効本数と available（単一の真実源）
```
effective = len(slice)    # 実際に統計へ用いた末尾スライス長（window=None で n）
available = effective >= MIN_EFFECTIVE_BARS(=2)
```
`available` の真実源は入力 `window` 値ではなく**実スライス長**である（窓長 1 や n=1 等で
帯が潰れる場合の整合を保証）。`available=False` のとき 8 バンドは全 NaN。

### 5.6 8 バンド投影（`compute_hl_band` / `hl_band_levels`）
`up_*` = High 側 = **加算側**（L220-223, StdDevArray[1..4]）、`dn_*` = Low 側 = **減算側**
（L224-227, StdDevArray[5..8]）。投影規則を `normalize` で 1 度だけ選択する:
- `normalize=True`（比率・乗算投影。`off_*_k = band_upper(r_*, dev_k)`）:
```
up_k = close_ref × (1 + off_high_k)        dn_k = close_ref × (1 − off_low_k)
```
- `normalize=False`（絶対・加減算投影。後方互換）:
```
up_k = close_ref + band_upper(dist_high, dev_k)    dn_k = close_ref − band_upper(dist_low, dev_k)
```
（k ∈ {067,165,196,258}）。

### 5.7 丸め・補間方式
- 元コードに `NormalizeDouble` は無く、float 精度で実装（ガイド §4.1、int 切り捨ても持ち込まない）。
- 標準偏差は母分散ベース（÷N, MT4 `iBandsOnArray` 準拠）。

## 6. Entities / 成果物（出力データ）
`build_hl_band` の DataFrame（index=入力 index）。**`normalize` に依らず常に絶対距離**を返す
（`compute_hl_band` とは独立に `compute_distances` を呼ぶため `hl_band_levels` の比率化に無影響）:
| 列 | 意味 |
|---|---|
| `hlband_dist_high`（`DIST_HIGH_COLUMN`） | `|high - close|` 絶対距離。warm-up/NaN なし。 |
| `hlband_dist_low`（`DIST_LOW_COLUMN`） | `|low - close|` 絶対距離。warm-up/NaN なし。 |

> `HlBandResult.dist_high` / `dist_low`（DTO フィールド）は `compute_hl_band` の `normalize` に
> 追従し、`normalize=True` では**比率** `|X-C|/C`、`False` では**絶対距離** `|X-C|` を保持する
> （フィールド名は下流影響回避のため `dist_*` 据え置き）。上記 DataFrame 2 列とは別経路。

スカラ参照値は時系列ではなく価格軸の水平参照値のため成果物 DataFrame と分離し辞書で提供:
- `hl_band_levels` → `{up_067, up_165, up_196, up_258, dn_067, dn_165, dn_196, dn_258,
  close_ref, available}`（overlay 8 バンド + 起点 + 算出可否）。`available=False`
  （有効本数 < 2）のとき 8 バンドは全 NaN。

EMPTY_VALUE 相当の非描画点は本指標では発生しない（距離・比率は全バー定義）。ただし有効本数
不足時は `available=False`・8 バンド NaN（描画側は非表示）。

## 7. Output（描画）
- **overlay 専用**（separate ペインなし。元 `indicator_chart_window`）。
- matplotlib（`plot_hl_band`）: 1 図 1 段（メイン軸）。価格（high/low/close 参考ライン）＋
  水平バンド線 8 本（上側 4 / 下側 4, LimeGreen, `axhline`）＋ 起点 close_ref の参照線
  （点線・参考）。プロット用ヒストグラムは持たない。
- lightweight-charts（`add_hl_band`）: メイン chart へ `horizontal_line` ×8
  （up_*/dn_*, LimeGreen, SOLID）。価格系列の描画は**呼び出し側前提**（本関数はバンドのみ
  追加）。各線の `text` は対応する levels キー（値とライン名を一致・ガイド §5）。多数線のため
  `price_line=False` / `price_label=False`（ガイド §6）。`lightweight_charts` は import せず
  duck typing で受ける。

## 8. Exception（異常系）
- high/low/close 列欠落: `KeyError`（`build_hl_band` / `hl_band_levels` / `add_hl_band` /
  `plot_hl_band`）。
- `N<2`（`close[-2]` 不在）: `ValueError`（`compute_hl_band` / 経由する `hl_band_levels` /
  `add_hl_band` / `plot_hl_band`）。
- `window<1`（int の 0・負。直近 W 本の窓として無意味）: `ValueError`（`compute_hl_band` /
  経由する `hl_band_levels`）。`window=None` は全長として許容。
- `normalize=True` かつ `close<=0` を含む（比率の 0 除算ガード）: `ValueError`
  （`compute_ratios` / 経由する `compute_hl_band` / `hl_band_levels`）。
- high/low/close 長不一致: `ValueError`（`compute_distances` / `compute_ratios`）。
- 時刻列（time/date/DatetimeIndex）解決不可・明示時刻列不在: `KeyError`（`add_hl_band`）。
- CSV 必須列（open/high/low/close）欠落・時刻列欠落: `KeyError`（`load_ohlc_csv`）。
- CSV ファイル不在: `FileNotFoundError`（`load_ohlc_csv`）。
- CSV 空（0 行）: `ValueError`（`load_ohlc_csv`）。

## 9. 元 MQL からの差分

### 後方互換モード（`window=None, normalize=False`）で 1:1 再現を保証する点
> 既定は比率正規化＋因果窓の拡張版（後述）であり、以下の 1:1 再現は**後方互換モード**
> （`window=None, normalize=False`）でのみ保証する（旧実装と bit 一致）。
- 距離: `dist_high = |high - close|` / `dist_low = |low - close|`（全バー、warm-up なし）。
- 距離帯: `平均 + {0.67,1.65,1.96,2.58}×母標準偏差`（÷N・全系列）。
- 8 投影の符号: **High 側 = close_ref への加算 / Low 側 = close_ref からの減算**。
- 起点: `close_ref = close[-2]`（= `iClose(...,1)` = 1 本前の終値）を 1:1 再現。

### 意図的に変えた / 前提化した点（根拠）
1. **`close[-2]` 起点（`Close[1]`）の 1:1 再現**: 元 `iClose(inpSymbol, inpTimeFrame, 1)` は
   shift=1 = 1 本前の確定足終値。昇順（古→新）配列では末尾から 2 番目 = `close[-2]`。最新足
   `close[-1]` ではないことを明示固定する（テストで `close_ref == 12.0`（_CLOSE の末尾-2）を固定）。
2. **母σ÷N の 1:1 再現**: `iBandsOnArray` の標準偏差は母分散（÷N）であり、標本σ（ddof=1）を
   使わない。`band_upper` は `sqrt(mean((x-mean)^2))` で再現する（テストは母σ と標本σ で値が
   異なる discriminating input で固定）。
3. **MT4 描画オブジェクト（OBJ_TREND）を水平線で再表現**: 元は `ObjectCreate(OBJ_TREND,
   Time[1]→Time[0])` の実質水平トレンドライン 8 本を、確定足更新（`iTime(...,1)` 変化）ごとに
   `ObjectDelete` → `ObjectCreate` で更新する。移植先（matplotlib `axhline` / lwc
   `horizontal_line`）はオブジェクトのライフサイクル管理を持たず、投影値 8 本のみを水平線として
   描く（SPEC §2）。
4. **既存 `profit_hlband`（アンダースコア無し）とは別物**: `profit_hlband` は高安レンジ
   `range = high - low` の全系列平均・母σ帯を**最新足 H/L** へ投影し、**separate ヒストグラム
   ＋ overlay 8 本**の二系統を描く。本 `profit_hl_band`（アンダースコア版）は**距離
   `|H-C|`/`|L-C|`** を、**起点 close[-2]** へ投影し、**overlay 専用**（ヒストグラム無し）で描く。
   計算定義・起点・描画系統がすべて異なる別指標である。
5. **`int` 切り捨て・`NormalizeDouble` は持ち込まない**（ガイド §4.1）。float 精度で実装。
6. **input パラメータなし（計算 period 不在）**: 元 `input` は `inpSymbol`/`inpTimeFrame`
   （シンボル・時間足の参照引数）のみで、計算 period・係数等の最適化パラメータは存在しない。
   移植先は現在系列をそのまま使い、計算引数を追加しない。
7. **`N>=2` ガードの新設**: 元 MQL4 はチャート供給データが常に 2 本以上ある前提で `iClose(1)` を
   参照する。移植先は CSV 等任意入力を受けるため、`close[-2]` が定義不能な `N<2` に対し明示的
   `ValueError` を投げる（`compute_hl_band`）。
8. **比率正規化（`normalize=True`・既定）の新設**: 元 MQL は絶対距離 `|X-C|` を全系列で集約し
   `close_ref±band` で加減算投影する。移植先の既定はこれを per-bar 比率 `r = |H−C|/C` に置換し
   （価格水準依存の是正・スケール不変）、`up_k = close_ref·(1 + off_high_k)` /
   `dn_k = close_ref·(1 − off_low_k)` で乗算投影する（`off_*_k = band_upper(r_*, dev_k)`）。
   旧挙動は `normalize=False` で bit 一致で残す。
9. **因果窓（`window=120`・既定）の新設**: 帯幅統計を末尾 W 本 `series[-window:]` に限定し、
   履歴長非依存・look-ahead 是正とする。`window=None` で全系列（後方互換）。`close_ref` は窓に
   依らず `close[-2]` を維持する。`window<1` は `ValueError`。
10. **`available` フラグの新設**: 有効本数（= 実スライス長 `len(series[-window:])`、`window=None`
    で n）が `MIN_EFFECTIVE_BARS=2` 未満なら帯算出不能とし `available=False`・8 バンド全 NaN。
    `available` の真実源は入力 `window` 値ではなく**実スライス長**である（単一の真実源）。

### bit-exact について
- **MT4 純正との bit-exact は非保証**: MT4 実機の参照 CSV（`iBandsOnArray` 出力 / 最終投影
  StdDevArray の CSV）が無いため、浮動小数の最終桁は実機と厳密一致しない可能性がある。完全
  一致が必要な場合は参照 CSV による回帰固定が必要。
