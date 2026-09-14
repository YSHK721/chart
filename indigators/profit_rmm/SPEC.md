# PRO!fitRMM 移植仕様書

## 1. Objective（目的）
4 つのオシレーター（**iRSI(Typical) / iWPR(+100) / iMFI / MAROD**）を、それぞれ
「系列平均 ±3σ のスパンで正規化した単位距離」へ funLevelCount で変換・合算した
**符号付きレベルカウント（市場の合成「温度」）** を、別ウィンドウ [-10,10] の
ヒストグラム 1 本で可視化する。各オシレーターの強気/弱気の偏差を 0 を基準に合成する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（4 オシレーター → funLevelCount 採点 → 合算 level_count → σ6 水準）/
  描画（separate window [-10,10] のヒストグラム 1 本 + σ6 水準線）/ 入力（CSV → OHLCV）。
- **対象外**: **OBJ_RECTANGLE による背景色帯**（元コードがレベルカウントの σ 区分ごとに
  `ObjectCreate(...,OBJ_RECTANGLE,...)` で塗る装飾）は**描画関心であり移植対象外**
  （先例 profit_adx_needle と同方針）。ブローカー接続・チャートデータ供給、アラート、
  最適化入力、リアルタイム差分再計算（バッチ全件計算で代替）も対象外。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fitRMM.mq4`（Copyright 2015, PRO!fit Investars）。
- 種別: MQL4。`#property indicator_separate_window`、`indicator_buffers 1`、
  `indicator_maximum 10` / `indicator_minimum -10`、`indicator_color1 clrLime`、
  `indicator_levelcolor C'84,84,84'`、`indicator_levelstyle STYLE_SOLID`。
  バッファ `ExtBufferLevelCount`（`SetIndexStyle(0, DRAW_HISTOGRAM)`）。
- input パラメータ: `int inpOscillatorPeriod = 6`、`int inpMovingAveragePeriod = 6`。
  `inpOscillatorPeriod < 2` のとき `return(INIT_FAILED)`（本移植は `ValueError` に対応）。
- 時系列の向き: `ResBuffer*[i]=iXXX(...,i)`（index 0 = 最新足）。本移植は**昇順
  （古い→新しい）** で扱い、平均/標準偏差/EMA を昇順で計算する。
- 使用する標準/ライブラリ関数:
  - `iRSI(NULL,0,period,PRICE_TYPICAL,i)` … RSI（適用価格 Typical）。
  - `iMFI(NULL,0,period,i)` … Money Flow Index。
  - `iWPR(NULL,0,period,i)+100` … Williams %R（+100 でオフセット）。
  - `iMA(NULL,0,maPeriod,0,MODE_EMA,PRICE_TYPICAL,i)` … Typical 価格の EMA。
  - funLevelCount（4 ケース採点）、平均・母標準偏差（σ スパン / σ6 水準）。

## 4. Input（入力）
- 必須列: `open` / `high` / `low` / `close` / **`volume`**（列名の大小不問）。
  `volume` は iMFI（Money Flow）に必須。`open` は計算未使用だが OHLCV 整合のため必須化。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 4 オシレーター（昇順, period=`osc_period`）
- **iRSI**（`compute_rsi`, 共有 mql_builtins の再公開）: 入力＝Typical 価格
  `(H+L+C)/3`。Wilder 平滑。`neg!=0 → 100-100/(1+pos/neg)`、`neg==0&pos!=0 → 100`、
  `neg==0&pos==0 → 50`（flat→50）。warm-up `i<period → 0`、`len<=period → 全 0`。
- **iWPR**（`compute_wpr`, 権威 `WPR.mq5` 準拠 → 値に `+100`）: 窓 `[i-period+1..i]`
  の `maxH/minL` から `maxH!=minL → -(maxH-close)*100/(maxH-minL)`、`maxH==minL →
  前値 wpr[i-1]`（flat→前値）。**warm-up は `i<period-1`**（iRSI/iMFI の `i<period`
  とは 1 本ズレる）。`n<period → 全 0`。range は素値 [-100,0]、+100 後 [0,100]。
- **iMFI**（`compute_mfi`, 共有 mql_builtins の再公開）: `TP=(H+L+C)/3`、`MF=TP*vol`、
  窓内 `TP[j]>TP[j-1]→正MF`/`<→負MF`/`==→加算しない`（非対称）。`負MF==0 → 100`、
  それ以外 `100-100/(1+正MF/負MF)`。warm-up `i<period → 0`。
- **MAROD**（`compute_marod`）: `ma = EMA(Typical, ma_period)`、
  `marod = (Typical - ma)/ma*100`（float 精度・`int` 切り捨てなし）。

### 5.2 σ スパン（`oscillator_span`, 全系列母σ±3σ）
各オシレーター系列 `x` について `avg=mean(x)`、`dev=母σ=sqrt(mean((x-avg)^2))`、
`x3p=avg+3dev`、`x3m=avg-3dev`、`span = x3p - x3m`。
**クランプ非対称**: RSI/WPR/MFI は `clamp=True`（`x3p=min(100,x3p)`,
`x3m=max(0,x3m)`）、**MAROD のみ `clamp=False`**（素値）。

### 5.3 funLevelCount（`level_count_score`, 4 ケース採点）
```
case0: r=(span-50)/200; return ((osi-50)/r)/100
case1: r=(span-50)/200; return -((50-osi)/r)/100
case2: r=(span/2)/200;  return ((osi-r)/r)/100
case3: r=(span/2)/200;  return -((r-osi)/r)/100
```
合算（各バー i, `compute_rmm`）:
```
RSI : rsi[i]>50 → +case0 ; <50 → +case1 ; ==50 → +0
WPR : wpr[i]>50 → +case0 ; <50 → +case1 ; ==50 → +0   # wpr は +100 済み
MFI : mfi[i]>50 → +case0 ; <50 → +case1 ; ==50 → +0
MAROD: marod[i]>0 → +case3 ; <0 → +case2 ; ==0 → +0
level_count[i] = 上記 4 採点の和
```
warm-up バーも採点に含める（1:1 再現）。

### 5.4 σ6 水準（`compute_rmm_levels`, level_count の母σ）
`avg=mean(level_count)`、`dev=母σ`:
```
up_1s=avg+dev, up_2s=avg+2dev, up_3s=avg+3dev,
dn_1s=avg-dev, dn_2s=avg-2dev, dn_3s=avg-3dev
```

## 6. Entities / 成果物（出力データ）
- `build_rmm` の DataFrame（index=入力 index）: 列 `rmm_lc`（= level_count, 符号付き,
  描画対象のヒストグラム値）。
- `rmm_levels` は level_count の σ6 水準辞書（`up_1s..dn_3s` の 6 要素）をスカラ提供
  （時系列ではないため成果物 DataFrame と分離）。
- `compute_rmm` は `RmmResult`（`level_count / rsi / wpr / mfi / marod / lc_levels`,
  全 ndarray `writeable=False`・frozen DTO）。

## 7. Output（描画）
- 別ウィンドウ（separate window）型・縦軸 [-10,10] 固定（元 indicator_minimum/maximum）。
- matplotlib（`src/plot.py`）: 下段ペインにレベルカウント・ヒストグラム 1 本
  （clrLime, 0 基準線あり）+ σ6 水準線（グレー C'84,84,84' の水平線）、`set_ylim(-10,10)`。
- lightweight-charts（`src/lwc_chart.py`）: `create_histogram`（名前 `rmm_lc`, clrLime,
  `price_line=False`, `price_label=False`）+ σ6 水準線 6 本を `horizontal_line`。
  `lightweight_charts` は import せず duck typing で受ける。
- OBJ_RECTANGLE 背景色帯は対象外（§2）。

## 8. Exception（異常系）
- OHLCV 列欠落（volume 含む）: `KeyError`（`build_rmm` / loader / `add_rmm`）。
- HLCV 長不一致: `ValueError`（`compute_rmm` / `compute_mfi`）。
- `osc_period < 2`: `ValueError`（元 `INIT_FAILED` に対応）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（`add_rmm`）。

## 9. 元 MQL からの差分

### 一致を保証する点（1:1 再現）
- **iRSI flat→50**（`neg==0&pos==0`）、**iMFI 負MF==0→100**、**iWPR flat→前値**を
  それぞれ 1:1 再現。**iWPR の warm-up は `i<period-1`**（権威 WPR.mq5 準拠。iRSI/iMFI の
  `i<period` とは 1 本ズレる）。iWPR は本パッケージ内の新規実装（RMM / RMMMACD の 2
  消費者・バッチ後集約方式）。
- funLevelCount 4 ケース採点・4 オシレーター合算・σ スパンのクランプ非対称
  （RSI/WPR/MFI クランプ、MAROD 非クランプ）・σ6 水準（母σ÷N）。

### 意図的に変えた / 前提化した点（根拠）
1. **ゼロ割をガードしない（1:1 維持）**: funLevelCount の `r==0`（`span==50` で
   case0/1、`span==0` で case2/3）や MAROD の `ma==0` は、元コードがガードしないため
   本移植も**ガードせず `inf` / `nan` を許容する**。退化入力（定数系列等）でのみ発生し、
   通常の市場データでは発生しない。numpy float 演算で例外を投げず `inf`/`nan` を返す。
2. **iRSI / iMFI / iWPR は共有 `mql_builtins`、funLevelCount / MAROD は共有
   `profit_system` を再利用**（import 再公開で in-package 参照面を維持）。
   **EMA / typical_price も共有層を再利用**（`moving_averages.exponential_ma_on_buffer`
   / `common.typical_price`）。共有層の正準実装に一本化し、複製による乖離を排除する。
3. **`int` 切り捨ては持ち込まない**（float 精度で実装。MAROD など）。
4. **ビット完全一致は非保証**: MT4 実機の参照 CSV（各 iXXX 出力 / 最終ヒストグラム）が
   無いため、EMA 初期化・warm-up 区間は実機と厳密一致しない可能性がある。完全一致が
   必要な場合は参照 CSV による回帰固定が必要。
5. **OBJ_RECTANGLE 背景色帯は移植しない**（描画装飾・対象外。§2）。

## 10. テスト
- `tests/test_core.py` / `tests/test_rmm.py`（実装済み）: 計算・成果物層の検証。
- `tests/test_lwc_chart.py`: Fake チャートでヒスト 1 本・σ6 水準線 6 本・name 一致・
  値一致・price フラグ・異常系（必須列欠落 / volume 欠落 / 時刻解決不可）を検証。
- 実行: `cd indicators/profit_rmm && python -m pytest -q`。
