# PRO!fit_HLBand 移植仕様書

## 1. Objective（目的）
High-Close / Low-Close の絶対距離 `dist_high = |high - close|` / `dist_low = |low - close|`
の系列全体の `平均` と `母標準偏差帯`（dev = 0.67 / 1.65 / 1.96 / 2.58）を算出し、起点終値
`close_ref = close[-2]`（元 `iClose(...,1)`）へ、High 側距離帯を**加算**（上側 4 本）・Low 側
距離帯を**減算**（下側 4 本）して価格軸へ投影した overlay バンド 8 本を、メインチャートに
重ねて描く。終値からの High/Low 乖離の統計的到達目安（±σ 距離）を、最新確定足の終値を起点に
可視化する。本指標は overlay 専用（separate ウィンドウ・ヒストグラムを持たない）。

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
- 時刻列: 描画アダプタ（`add_hl_band`）は `time`/`date` 列または `DatetimeIndex` を時刻解決
  に用いる（overlay の水平線は価格軸スカラだが、元が時系列チャートへの重畳であること・先例の
  異常系整合のため時刻解決可能性を要求する）。
- 前提: 行は時系列昇順。最低 **2 行**（`close[-2]` 起点のため `N>=2`）。欠損なし（NaN 前提
  処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 距離（`compute_distances`）
全バー `i`（warm-up なし・NaN なし）:
```
dist_high[i] = |high[i] - close[i]|     # 元 MathAbs(iHigh(i) - iClose(i))（L205）
dist_low[i]  = |low[i]  - close[i]|     # 元 MathAbs(iLow(i)  - iClose(i))（L206）
```

### 5.2 距離帯（`band_upper`, `iBandsOnArray` MODE_UPPER 相当）
距離系列全長の算術平均 `mean` と**母**標準偏差 `sigma = sqrt(mean((x-mean)^2))`（÷N）から:
```
band_upper(dist, dev) = mean(dist) + dev * sigma(dist)
```
dev ∈ {0.67, 1.65, 1.96, 2.58}（`HL_BAND_DEVS`）。

### 5.3 起点終値（`close_ref`）
```
close_ref = close[-2]     # 元 iClose(inpSymbol, inpTimeFrame, 1) = 1 本前 = 昇順末尾-1
```

### 5.4 8 バンド投影（`compute_hl_band` / `hl_band_levels`）
High 側 = **加算**（L220-223, StdDevArray[1..4]）、Low 側 = **減算**（L224-227,
StdDevArray[5..8]）:
```
up_067 = close_ref + band_upper(dist_high, 0.67)    dn_067 = close_ref - band_upper(dist_low, 0.67)
up_165 = close_ref + band_upper(dist_high, 1.65)    dn_165 = close_ref - band_upper(dist_low, 1.65)
up_196 = close_ref + band_upper(dist_high, 1.96)    dn_196 = close_ref - band_upper(dist_low, 1.96)
up_258 = close_ref + band_upper(dist_high, 2.58)    dn_258 = close_ref - band_upper(dist_low, 2.58)
```

### 5.5 丸め・補間方式
- 元コードに `NormalizeDouble` は無く、float 精度で実装（ガイド §4.1、int 切り捨ても持ち込まない）。
- 標準偏差は母分散ベース（÷N, MT4 `iBandsOnArray` 準拠）。

## 6. Entities / 成果物（出力データ）
`build_hl_band` の DataFrame（index=入力 index）:
| 列 | 意味 |
|---|---|
| `hlband_dist_high`（`DIST_HIGH_COLUMN`） | `|high - close|` 距離。warm-up/NaN なし。 |
| `hlband_dist_low`（`DIST_LOW_COLUMN`） | `|low - close|` 距離。warm-up/NaN なし。 |

スカラ参照値は時系列ではなく価格軸の水平参照値のため成果物 DataFrame と分離し辞書で提供:
- `hl_band_levels` → `{up_067, up_165, up_196, up_258, dn_067, dn_165, dn_196, dn_258,
  close_ref}`（overlay 8 バンド + 起点）。

EMPTY_VALUE 相当の非描画点は本指標では発生しない（距離は全バー定義）。

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
- high/low/close 長不一致: `ValueError`（`compute_distances`）。
- 時刻列（time/date/DatetimeIndex）解決不可・明示時刻列不在: `KeyError`（`add_hl_band`）。
- CSV 必須列（open/high/low/close）欠落・時刻列欠落: `KeyError`（`load_ohlc_csv`）。
- CSV ファイル不在: `FileNotFoundError`（`load_ohlc_csv`）。
- CSV 空（0 行）: `ValueError`（`load_ohlc_csv`）。

## 9. 元 MQL からの差分

### 一致を保証する点（原挙動の 1:1 再現）
- 距離: `dist_high = |high - close|` / `dist_low = |low - close|`（全バー、warm-up なし）。
- 距離帯: `平均 + {0.67,1.65,1.96,2.58}×母標準偏差`（÷N）。
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

### bit-exact について
- **MT4 純正との bit-exact は非保証**: MT4 実機の参照 CSV（`iBandsOnArray` 出力 / 最終投影
  StdDevArray の CSV）が無いため、浮動小数の最終桁は実機と厳密一致しない可能性がある。完全
  一致が必要な場合は参照 CSV による回帰固定が必要。
