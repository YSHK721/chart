# PRO!fitSTC 移植仕様書

## 1. Objective（目的）
終値の高安レンジ位置（Stochastic %K, fast, 既定 period=70）を別ウィンドウのオシレーター
線で可視化し、その系列全体の `平均 ±1.00σ / ±1.96σ`（P1/P2/M1/M2）を水準線として描く。
%K がレンジ内のどこに位置するか（買われすぎ／売られすぎの相対位置）と、その統計的乖離を
表現する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（%K → σ 帯 4 本 → subwindow 範囲）/ 描画（separate window の
  オシレーター線 + σ 水準線 4 本）/ 入力（CSV → OHLC）。
- 対象外: ブローカー接続・チャートデータ供給、アラート、最適化入力、リアルタイム差分
  再計算（バッチ全件計算で代替。ガイド §3/§6）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fitSTC.mq4`（実体 PRO!fitOscillator ver1.00,
  Copyright 2015, PRO!fit Investars）。MQL4。
- 種別: `#property indicator_separate_window`、`indicator_buffers 10`（実使用は
  OnInit で `IndicatorBuffers(1)`）/ `indicator_plots 1`。
- バッファ/プロット: バッファ `ExtBufferOscillator`（プロット 0, **`SetIndexStyle(0,
  DRAW_LINE)`**, `indicator_color1 DarkGreen`, `indicator_width1 2`）。
- σ 水準線: `indicator_levelcolor C'84,84,84'`（グレー）, `indicator_levelstyle
  STYLE_SOLID`。`funIndicatorSet` が `IndicatorSetDouble(INDICATOR_LEVELVALUE, i, ...)`
  で水準値を設定（P1/P2/M1/M2）。
- input パラメータ: `int inpPeriodOscillator = 70`（オシレーター期間）。
  **`inpPeriodOscillator < 2` で `return(INIT_FAILED)`**（OnInit のガード）。
- 時系列の向き: `ArraySetAsSeries` 明示なし。`iStochastic(...,i)` で index 0 = 最新足
  （系列順）として埋める。本移植は**昇順（古→新）**で扱い、%K・平均・標準偏差を昇順で
  計算する（ガイド §4.3）。本指標は窓内 min/max のため向きによる差は出ない。
- 使用する標準/ライブラリ関数:
  - `iStochastic(NULL,0,inpPeriodOscillator,1,1,MODE_EMA,0,MODE_MAIN,i)` … %K 本線。
    Kperiod=inpPeriodOscillator, Dperiod=1, slowing=1, price_field=0(Low/High)。
  - `iBandsOnArray(ExtBufferOscillator,0,rates_total,dev,0,{1|2},0)` … 配列全長
    SMA ± dev×母標準偏差（MODE_UPPER=1 / MODE_LOWER=2）。
  - `IndicatorSetDouble(INDICATOR_MINIMUM=StcLCStdDevArray[4]=M2,
    INDICATOR_MAXIMUM=StcLCStdDevArray[2]=P2)`。

## 4. Input（入力）
- 必須列: `high` / `low` / `close`（列名の大小不問）。CSV ローダ `load_ohlc_csv` は
  `open/high/low/close` を必須とする（`open` は %K 計算には不使用）。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 オシレーター %K（`compute_stochastic`, period=70）
昇順 OHLC、各バー `a`（`a >= period-1`。`a < period-1` は warm-up）:
```
LL   = min(low[a-period+1 .. a])
HH   = max(high[a-period+1 .. a])
rng  = HH - LL
%K[a] = 100 * (close[a] - LL) / rng       ; rng != 0
%K[a] = 0                                  ; rng == 0（ゼロ割は 0）
%K[a] = 0                                  ; a < period-1（warm-up は 0）
```
- 元 `iStochastic` は slowing=1 / Dperiod=1 のため **MODE_EMA 平滑が恒等（vestigial）**
  となり、平滑を実装しない（生 %K に一致）。price_field=0 のため Low/High でレンジを取る。
- warm-up は元 iStochastic 既定どおり **0**（NaN ではない）。ゼロ割（HH==LL）も **0**。

### 5.2 σ 水準（`compute_osc_levels`, `iBandsOnArray` 相当）
オシレーター全長（**warm-up の 0 を除外せず**）の `mean`（算術平均）と母標準偏差
`std = sqrt(mean((x-mean)^2))`（÷N）から:
```
P1 = mean + 1.00*std    # iBandsOnArray dev=1.00 MODE_UPPER
P2 = mean + 1.96*std    # iBandsOnArray dev=1.96 MODE_UPPER
M1 = mean - 1.00*std    # iBandsOnArray dev=1.00 MODE_LOWER
M2 = mean - 1.96*std    # iBandsOnArray dev=1.96 MODE_LOWER
```

### 5.3 subwindow 範囲
```
sub_min = M2   # IndicatorSetDouble(INDICATOR_MINIMUM, StcLCStdDevArray[4])
sub_max = P2   # IndicatorSetDouble(INDICATOR_MAXIMUM, StcLCStdDevArray[2])
```

### 5.4 丸め・補間方式
- 元コードに `NormalizeDouble` は無く、float 精度で実装（ガイド §4.1, int 切り捨ても持ち込まない）。
- 標準偏差は母分散ベース（÷N, MT4 iBands 準拠）。

## 6. Entities / 成果物（出力データ）
`build_stc` の DataFrame（index=入力 index）:
| 列 | 意味 |
|---|---|
| `stc_osc`（`OSC_COLUMN`） | %K オシレーター値（描画対象, 線）。warm-up は 0。 |

σ 水準線は `stc_levels`（`P1/P2/M1/M2` + `sub_min(=M2)` / `sub_max(=P2)`）でスカラ提供
（時系列ではなく価格軸の水平参照値のため成果物 DataFrame と分離）。
EMPTY_VALUE 相当の非描画点は本指標では発生しない（warm-up も 0 で全バー算出）。

## 7. Output（描画）
- 別ウィンドウ（separate window）型。
- matplotlib: 下段ペインに**オシレーター線**（DarkGreen, linewidth 2 ＝ 元 width2）+
  σ 水準線 4 本（P1/P2/M1/M2, SOLID, グレー C'84,84,84'）。y 範囲 sub_min(=M2)〜sub_max(=P2)。
- lightweight-charts: `create_line`（name=`stc_osc`, DarkGreen, width 2, style solid,
  price_line/label=False）+ σ 水準線 4 本を `horizontal_line`（SOLID, グレー）。塗り不使用
  （ガイド §6）。ライン名は値列名（`stc_osc`）と完全一致（ガイド §5）。

## 8. Exception（異常系）
- HLC 列欠落: `KeyError`（`build_stc` / `stc_levels` / `lwc_chart`）。
- CSV 必須列（open/high/low/close）欠落・時刻列欠落: `KeyError`（`load_ohlc_csv`）。
- CSV ファイル不在: `FileNotFoundError`（`load_ohlc_csv`）。
- HLC 長不一致: `ValueError`（`compute_stochastic`）。
- `period < 2`: `ValueError`（`compute_stochastic`。元 OnInit の `INIT_FAILED` に対応）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（`lwc_chart`）。

## 9. 元 MQL からの差分

### 一致を保証する点（原挙動の 1:1 再現）
- %K 算式（直近 period 本の高安レンジ位置, fast, price_field=0=Low/High）。
- **warm-up（`a<period-1`）は 0**（元 iStochastic 既定。NaN ではない）。
- **ゼロ割（HH==LL）は 0**。
- σ 帯（P1/P2/M1/M2）= 全系列 `平均 ±{1.00,1.96}×母標準偏差`。**統計に warm-up の 0 が
  混入する**点も元挙動どおり除外せず再現する。
- subwindow 範囲 `sub_min=M2 / sub_max=P2`。
- `period < 2` で例外（元 OnInit の INIT_FAILED）。

### 中立記載（原挙動の 1:1 再現として保持し "改善" しない）
- **warm-up 0・ゼロ割 0・σ 統計への 0 混入**は、統計的には平均を 0 方向へ引き、標準偏差を
  膨らませる効果があるが、これは元 MQL の挙動そのものである。除外・補正は行わず**原挙動と
  して 1:1 再現する**（良し悪しの判断を持ち込まない中立記載）。

### 意図的に変えた / 前提化した点（根拠）
1. **MODE_EMA 平滑の非実装（vestigial）**: 元 `iStochastic` は Dperiod=1 / slowing=1 の
   ため MODE_EMA 平滑が恒等変換となり、MODE_MAIN（%K 本線）は生 %K に一致する。よって平滑を
   実装しない（出力に影響しない）。
2. **Stochastic %K・σ 帯は in-package 実装（将来 common 昇格余地）**: 本指標専用の
   プリミティブとして `src/core.py` 内に閉じる。描画・pandas 非依存の独立関数として保ち、
   将来 `common/` へシグネチャ不変で昇格する余地を残すが、現時点では YAGNI に従い昇格しない。
3. **`int` 切り捨て・`NormalizeDouble` は持ち込まない**（ガイド §4.1）。元コードに整数化・
   丸めは無く、float 精度で実装。
4. **MT4 純正との bit-exact は非保証**: MT4 実機の参照 CSV（iStochastic 出力 / 最終
   オシレーター CSV）が無いため、warm-up 区間・浮動小数の最終桁は実機と厳密一致しない可能性
   がある。完全一致が必要な場合は参照 CSV による回帰固定が必要。
