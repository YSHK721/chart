# PRO!fitOscillator 移植仕様書

## 1. Objective（目的）
8 つのサブオシレーターを `funLevelCount`（span=100 固定）で単位距離へ変換し、加重
**1/2/2/10/10/10/1/1** で合算した**符号付きレベルカウント**（市場の合成「温度」）と、
そのレベルカウント系列に対する **Spearman 順位相関指標（RCI）** を、別ウィンドウの
レベルカウント・ヒストグラム 1 本＋RCI 線 1 本で可視化する。σ6 水準線で偏差区分を示す。

## 2. Scope（範囲・対象外）
- 移植する: 計算（サブ 8 オシレーター → funLevelCount 採点 → 加重合算 level_count →
  σ6 水準 → Spearman RCI）/ 描画（separate window のヒストグラム 1 本＋RCI 線 1 本＋
  σ6 水準線）/ 入力（CSV → OHLCV）。
- **対象外**: **OBJ_RECTANGLE による背景色帯**（描画関心であり移植対象外。先例
  profit_rmm / profit_adx_needle と同方針）。ブローカー接続・チャートデータ供給、
  アラート、最適化入力、リアルタイム差分再計算（バッチ全件計算で代替）も対象外。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fitOscillator.mq4`（2015, PRO!fit Investars）。MQL4。
- `#property indicator_separate_window`、`indicator_buffers 10`、`indicator_plots 2`、
  `indicator_color1 DarkGreen`（プロット 0 = レベルカウント）、`indicator_color2 clrLime`
  （プロット 1 = RCI）、`indicator_levelcolor C'84,84,84'`、`indicator_levelstyle STYLE_SOLID`、
  `indicator_width1 2`、`indicator_height 100`。
  プロット 0: `ExtBufferLevelCount`（`SetIndexStyle(0, DRAW_HISTOGRAM)`）。
  プロット 1: `ExtBufferRCI`（`SetIndexStyle(1, DRAW_LINE)`）。
- input パラメータ:
  - `int inpPeriodOscillator = 6`（サブオシレーター期間）。
  - `int inpPeriodSTC_SLOW = 6`（iStochastic slowing ＝ D 期間）。
  - `int inpPeriodMA = 60`（MAROD の EMA 期間）。
  - `int inpPeriodRCI = 12`（RCI 期間）。
  - `bool direction = false`（RCI ソート方向）。
  - `inpPeriodOscillator < 2` のとき INIT_FAILED 相当（本移植は `ValueError`）。
- 時系列の向き: `ResBuffer*[i]=iXXX(...,i)`（index 0 = 最新足）。本移植は**昇順
  （古い→新しい）** で扱い、平均/母σ/EMA/RCI 窓を昇順で計算する。
- 使用する標準/ライブラリ関数: `iRSI` / `iMFI`（NULL,0,period）/ `iWPR(...)+100` /
  `iStochastic(NULL,0,osc,slow,slow,MODE_EMA,0,MODE_MAIN|MODE_SIGNAL)` / `iMA(MODE_EMA)` /
  funLevelCount（4 ケース採点, span=100 固定）/ `iBandsOnArray`（1.65/1.96/2.58σ）/
  RankPrices ＋ SpearmanRankCorrelation（RCI）。

## 4. Input（入力）
- 必須列: `open` / `high` / `low` / `close` / **`volume`**（列名の大小不問）。
  `volume` は iMFI（Money Flow）に必須。`open` は applied_price 経由で参照されうるため必須化。
- 時刻列（描画用）: `time` / `date` 列、または DatetimeIndex（lwc_chart）。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 サブ 8 オシレーター（昇順, period=`osc_period`）
- **iRSI**（`compute_rsi`, profit_rmm 複製）: 入力＝applied_price(LOW)。Wilder 平滑。
  `neg!=0 → 100-100/(1+pos/neg)`、`neg==0&pos!=0 → 100`、`neg==0&pos==0 → 50`。
  warm-up `i<=period → 0`。
- **iWPR**（`compute_wpr`, profit_rmm 複製 → 値に `+100`）: 窓 `[i-period+1..i]` の
  `maxH/minL` から `maxH!=minL → -(maxH-close)*100/(maxH-minL)`、`maxH==minL → 前値`。
  warm-up `i<period-1`。+100 後 [0,100]。
- **iMFI**（`compute_mfi`, profit_rmm 複製）: `TP=(H+L+C)/3`、`MF=TP*vol`、窓内
  `TP[j]>TP[j-1]→正MF`/`<→負MF`/`==→加算しない`。`負MF==0 → 100`、それ以外
  `100-100/(1+正MF/負MF)`。warm-up `i<period → 0`。
- **MAROD ×3**（`compute_marod`）: Typical/High/Low の各価格 `p` に対し
  `ma=EMA(p, ma_period)`、`marod=(p-ma)/ma*100`（float 精度・int 切り捨て無し）。
- **iStochastic main/signal**（`compute_istoch_main_signal`）: 生 %K
  `100*(close-LL)/(HH-LL)`（warm-up `a<period-1 → 0`、`HH==LL → 0`）から
  `main=EMA(rawK, slowing=stc_slow)`、`signal=EMA(main, d_period=stc_slow)` の**二段 EMA**。

### 5.2 funLevelCount 採点（span=100 固定）
各サブ値 `osi` を 4 ケースで採点する（`level_count_score`, ゼロ割ガード無し）::

    case0: r=(span-50)/200; ((osi-50)/r)/100
    case1: r=(span-50)/200; -((50-osi)/r)/100
    case2: r=(span/2)/200;  ((osi-r)/r)/100
    case3: r=(span/2)/200;  -((r-osi)/r)/100

- 50 ピボット系（RSI/WPR/MFI/STC）: `osi<50→case1`, `osi>50→case0`, `==50→0`。
- 0 ピボット系（MAROD ×3）: `marod<0→case2`, `marod>0→case3`, `==0→0`。

### 5.3 加重合算レベルカウント（`compute_level_count`）

    lc =  1*score(RSI_Low,50) + 2*score(WPR,50) + 2*score(MFI,50)
        + 10*score(MAROD_T,0) + 10*score(MAROD_H,0) + 10*score(MAROD_L,0)
        + 1*score(STC_signal,50) + 1*score(STC_main,50)

### 5.4 σ6 水準（`compute_levels2`, 母σ÷N）
`avg=mean(lc)`、`dev=母σ=sqrt(mean((lc-avg)^2))`::

    up_165=avg+1.65dev, up_196=avg+1.96dev, up_258=avg+2.58dev
    dn_165=avg-1.65dev, dn_196=avg-1.96dev, dn_258=avg-2.58dev
    sub_min=dn_196*1.5（元 StdDev[5]*1.5）, sub_max=up_196*1.5（元 StdDev[2]*1.5）

**LC クランプ無し**。subwindow 縦軸範囲は sub_min〜sub_max。

### 5.5 Spearman RCI（`compute_rci`）
各昇順バー `a`（`a<period-1 → 0`）::

    w[k] = int(level_count[a-k])  # int 切り捨て（0 方向）してから順位付け
    R2 = TrueRank（direction でソート方向・同値タイ平均）
    spearman = 1 - 6*Σ(R2[k]-(k+1))^2 / (period^3 - period)
    rci[a] = spearman * sigma_ref      # sigma_ref = dn_196（元 StcLCStdDevArray[5]）

## 6. Entities / 成果物（出力データ）
- `oscillator2_lc`（`LEVEL_COUNT_COLUMN`）: 加重合算レベルカウント（符号付き）。
- `oscillator2_rci`（`RCI_COLUMN`）: Spearman RCI。
- `oscillator2_levels`: σ6 水準辞書（up/dn 各 3 本）＋ `sub_min`/`sub_max`。
- warm-up は 0 で残る（NaN は発生しない）。

## 7. Output（描画）
- separate window。
- レベルカウント・ヒストグラム 1 本（`oscillator2_lc`, DRAW_HISTOGRAM, DarkGreen）。
- RCI 線 1 本（`oscillator2_rci`, DRAW_LINE, clrLime, 名称 "RCI"）。
- σ6 水準線 6 本（up_165/up_196/up_258/dn_165/dn_196/dn_258, グレー C'84,84,84', STYLE_SOLID）。
- subwindow 縦軸範囲 sub_min〜sub_max（**LC クランプ無し**）。
- 多数系列のため `price_line=False` / `price_label=False`。OBJ_RECTANGLE 背景帯は対象外。

## 8. Exception（異常系）
- 必須列欠落（open/high/low/close/**volume** いずれか）: `KeyError`（loader / 成果物層 / lwc_chart）。
- 時刻列欠落（time/date/DatetimeIndex いずれも無い）: `KeyError`（lwc_chart, 計算前に確定）。
- `osc_period<2` / `ma_period<2`: `ValueError`（core）。
- OHLCV 長不一致: `ValueError`（core）。

## 9. 元 MQL からの差分

### 9.1 原挙動バグの 1:1 再現（意図的・ユーザー承認済）
- **RSI ×3 の先頭 2 本（Typical/High）が上書きで消える**: 元コードは RSI_Typical →
  RSI_High → RSI_Low の順に `=`（代入）で 3 回上書きするため、最終的に残るのは
  **RSI_Low の採点のみ**。本移植は `compute_level_count_rsi_term` で RSI_Low 基底のみを
  採用し原挙動バグを 1:1 再現する（§4.4 相当）。
- **RCI の int 切り捨て**: 元コードは `int SortInt[]` / `int etalon` で LevelCount を
  **int 切り捨ててから順位付け**する。これは実装由来の切り捨てだが原挙動として 1:1 再現する
  （ユーザー承認済・§4.1 衝突を 1:1 再現で解決）。

### 9.2 元と一致を保証する点
- iStochastic は `main=EMA(生%K, slowing)`、`signal=EMA(main, Dperiod)` の**二段 EMA**。
- funLevelCount は **span=100 固定**（元コード第 2 引数固定）。
- 加重は **1/2/2/10/10/10/1/1**（元 L177-278 の係数）。
- σ6 は母σ（÷N）、sub_min/sub_max は ×1.5（元 INDICATOR_MIN/MAX）。
- 採点の符号・場合分け（50/0 ピボット, 4 ケース）。

### 9.3 構造上の注記
- 複製関数（`compute_rsi` / `compute_mfi` / `compute_wpr` / `compute_marod` /
  `compute_stochastic` / `level_count_score`）は同一性維持のため共通化しない
  （バッチ後に common へ集約予定）。
- 時系列は昇順（古→新）で扱う（元 index 0 = 最新足を反転）。
- **RCI の最新側 warm-up セル**: 元 MQL の RCI ループは `for(i=rates_total-period; i>=0; i--)`
  で最新側 `period-1` セルを書き込まず初期値 0 のまま残す。本移植は昇順で
  `a < period-1` を 0 とする。series 前提（index 0=最新）の反転で両者は一致する。
- **bit-exact は非保証**（volume の値定義・浮動小数演算順序・共有 EMA 実装に依存）。
