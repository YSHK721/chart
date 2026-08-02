# PRO!fitRSI 移植仕様書

## 1. Objective（目的）
適用価格（既定 Typical price）に対する相対力指数（Relative Strength Index, RSI,
既定 period=6）を別ウィンドウ（[0,100]）の RSI 線で可視化し、
**生 RSI 系列**全体の `平均 ±1/2/3σ`（p1/p2/p3, m1/m2/m3）と
中央線 50（mid50）を統計的水準線として描く。上昇幅と下降幅の Wilder 平滑比から相場の
過熱・冷却の相対強度を表現する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（iRSI → σ 7 水準）/ 描画（separate window [0,100] の
  RSI 線 ＋ σ 水準線 7 本）/ 入力（CSV → OHLC、**volume 不要**）。
- 対象外: **RSI の EMA 平滑線（元 `ExtMABuffer` / `InpMAPeriod`）**。σ 水準は元から
  **生 RSI 系列**に掛かる（§5.4）ため、平滑線は描画専用であり他の出力に影響しない。
  設定項目 `ma_period` ごと削除した（ユーザー承認 2026-08-02）。
- 対象外: ブローカー接続・チャートデータ供給、アラート、最適化入力、リアルタイム差分
  再計算（バッチ全件計算で代替。ガイド §3/§6）、OnInit の Object 一括削除・サブ
  ウィンドウラベル設定（描画フレームワーク固有の副作用）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fitRSI.mq4`（Copyright 2015, PRO!fit
  Investars, description "Relative Strength Index"）。MQL4。
- 種別: `#property indicator_separate_window`、`indicator_buffers 2`、
  `indicator_minimum 0` / `indicator_maximum 100`。
- バッファ/プロット: `ExtRSIBuffer`（プロット 0, `SetIndexStyle(0, DRAW_LINE)`,
  `indicator_color1 clrLime`）/ `ExtMABuffer`（プロット 1, `SetIndexStyle(1,
  DRAW_LINE)`, `indicator_color2 clrLime`）。
- 短名 `IndicatorShortName`: `switch(Apply)` により適用価格名が変わる
  （`"RSI-{適用価格名} (" + InpRSIPeriod + ")"`）。`SetIndexDrawBegin(0, InpRSIPeriod)`。
- σ 水準線: `indicator_levelcolor C'84,84,84'`（グレー）, `indicator_levelstyle
  STYLE_SOLID`。`IndicatorSetInteger(INDICATOR_LEVELS, 5)`（後続 SetDouble は index
  0..6 を設定するため実質 ±1/2/3σ ＋ 中央 50 の 7 水準）。
- input パラメータ:
  - `int InpRSIPeriod = 6`（RSI 期間）。**`InpRSIPeriod < 2` で `return(INIT_FAILED)`**。
  - `int Apply = 5`（RSI Method ＝ 適用価格選択。既定 5 = PRICE_TYPICAL）。
  - `int InpMAPeriod = 5`（EMA 平滑期間）。**本移植では非対応**（§2 対象外）。
- 時系列の向き: `ArraySetAsSeries` 明示なし。`iRSI(...,i)` / `iMAOnArray(...,i)` は
  index 0 = 最新足（系列順）として埋める。本移植は**昇順（古→新）**で計算する
  （ガイド §4.3）。Wilder 平滑・全系列統計のため向きによる差は出ない。
- 使用する標準/ライブラリ関数:
  - `iRSI(NULL, 0, InpRSIPeriod, <PRICE_*>, i)` … Wilder RSI（権威: MetaQuotes 公式
    `RSI.mq5`）。`diff=price[i]-price[i-1]`, seed=period 本平均, main=Wilder 平滑。
  - `iMAOnArray(ExtRSIBuffer, 0, InpMAPeriod, 0, MODE_EMA, i)` … RSI 系列の EMA 平滑。
  - `iStdDevOnArray(ExtRSIBuffer, 0, rates_total, 0, MODE_SMA, 0)` … **生 RSI 系列**
    全長の母標準偏差（÷N）。
  - `iMAOnArray(ExtRSIBuffer, 0, rates_total, 0, MODE_SMA, 0)` … **生 RSI 系列**全長
    の算術平均。**注意: σ 統計は EMA 平滑後の ExtMABuffer ではなく生 RSI の
    ExtRSIBuffer に掛ける**（元コードの引数が ExtRSIBuffer）。
  - `StDevA1..A6 = avg ± StDev×{1,2,3}`、`INDICATOR_LEVELVALUE index 4 = 50`。

### Apply（独自 input）→ 適用価格 写像表
| Apply | MQL4 定数 | 適用価格 | 共有 common.AppliedPrice | 短名の適用価格名 |
|---|---|---|---|---|
| 1 | PRICE_OPEN | Open | OPEN | Open price |
| 2 | PRICE_HIGH | High | HIGH | High price |
| 3 | PRICE_LOW | Low | LOW | Low price |
| 4 | PRICE_MEDIAN | (H+L)/2 | MEDIAN | Median price |
| 5（既定） | PRICE_TYPICAL | (H+L+C)/3 | TYPICAL | Typical price |
| 6 | PRICE_WEIGHTED | (H+L+2C)/4 | WEIGHTED | Weighted close price |
| default | PRICE_CLOSE | Close | CLOSE | Close price |

## 4. Input（入力）
- 必須列: `open` / `high` / `low` / `close`（列名の大小不問）。CSV ローダ
  `load_ohlc_csv` は `open/high/low/close` を必須とする。**volume は不要**
  （元 `iRSI` は出来高を参照しない）。
- 前提: 行は時系列昇順。欠損なし（NaN 前提の特別処理は持たない）。

## 5. Processing（計算定義）— 一意に

### 5.1 適用価格の選択（共有 common）
`Apply` を §3 写像表で `common.AppliedPrice` に写像し、共有
`common.applied_price(kind, open, high, low, close)` で価格系列を選択する（適用価格は
共有層を再利用し in-package 再実装しない）。既定 `Apply=5` → TYPICAL=(H+L+C)/3。

### 5.2 iRSI（`compute_rsi`, period=6）— 権威 Wilder（共有 `mql_builtins` 由来・再公開）
昇順 価格系列、`diff[i] = price[i] - price[i-1]`:
```
seed (i == period):
    pos = mean_{j=1..period}(max(diff[j], 0))
    neg = mean_{j=1..period}(max(-diff[j], 0))
main (i > period):  # Wilder 平滑
    pos[i] = (pos[i-1]*(period-1) + max(diff[i], 0)) / period
    neg[i] = (neg[i-1]*(period-1) + max(-diff[i], 0)) / period
```
ゼロ割の場合分け（一意・権威 = MetaQuotes 公式 `RSI.mq5` 準拠）:
```
neg != 0              -> RSI[i] = 100 - 100/(1 + pos/neg)
neg == 0 かつ pos != 0 -> RSI[i] = 100   （all-up window）
neg == 0 かつ pos == 0 -> RSI[i] = 50    （flat window。MFI の 100 とは異なる）
i < period            -> RSI[i] = 0      （warm-up は 0。NaN ではない）
rates_total <= period -> 全 0            （元 RSI.mq5 の早期 return）
```

### 5.3 EMA 平滑 — 移植しない
元 `iMAOnArray(ExtRSIBuffer, MODE_EMA, InpMAPeriod)` の平滑線は持たない（§2 対象外・
承認 2026-08-02）。σ 水準は元から生 RSI 系列に掛かるため、削除しても水準値は不変。

### 5.4 σ 7 水準（`compute_rsi_levels`）— **生 RSI 系列**に掛ける
**生 RSI 系列**（平滑系列ではない）**全長**（**warm-up の 0 を除外せず**）の
算術平均 `avg` と母標準偏差 `σ = sqrt(mean((x-avg)^2))`（÷N）から:
```
p1 = avg + 1σ    p2 = avg + 2σ    p3 = avg + 3σ
m1 = avg - 1σ    m2 = avg - 2σ    m3 = avg - 3σ
mid50 = 50.0     （元 INDICATOR_LEVELVALUE index 4 = 50, 固定中央線）
```
元 `iStdDevOnArray(ExtRSIBuffer, ...)` / `iMAOnArray(ExtRSIBuffer, MODE_SMA,
period=rates_total)` に対応（**引数が ExtRSIBuffer ＝ 生 RSI 系列**）。

### 5.5 丸め・補間方式
- 元コードに `NormalizeDouble` は無く、float 精度で実装（ガイド §4.1, int 切り捨ても
  持ち込まない）。
- 標準偏差は母分散ベース（÷N, MT4 iStdDev 準拠）。

## 6. Entities / 成果物（出力データ）
`build_rsi` の DataFrame（index=入力 index）:
| 列 | 意味 |
|---|---|
| `rsi`（`RSI_COLUMN`） | iRSI 値（描画対象, 線）。warm-up は 0。 |

σ 7 水準は `rsi_levels`（`p1/p2/p3/m1/m2/m3/mid50`）でスカラ提供（時系列ではなく価格軸の
水平参照値のため成果物 DataFrame と分離）。EMPTY_VALUE 相当の非描画点は本指標では発生
しない（warm-up も 0 で全バー算出）。

## 7. Output（描画）
- 別ウィンドウ（separate window）型。y 範囲 [0,100]（元 indicator_minimum 0 /
  indicator_maximum 100）。
- RSI 線の凡例: 元 `IndicatorShortName` "RSI-{適用価格名} ({period})"（Apply で適用
  価格名が変わる。例 Apply=5 → "RSI-Typical price (6)"。`plot.rsi_short_name`）。
- matplotlib: 下段ペインに **RSI 線**（Lime）＋ σ 水準線 7 本
  （±1/2/3σ は点線グレー C'84,84,84'、中央線 50 は実線）。
- lightweight-charts: `create_line` 1 本（name=`rsi`, clrLime, style solid,
  price_line/label=False）＋ σ 水準線 7 本を `horizontal_line`（SOLID, グレー）。多数線の
  ため price_line/label=False（ガイド §6）。ライン名は値列名（`rsi`）と完全
  一致（ガイド §5。Apply 依存の短名は lwc line name には用いず plot 凡例に限定）。
  `lightweight_charts` は import せず duck typing で受ける。

## 8. Exception（異常系）
- OHLC 列欠落: `KeyError`（`build_rsi` / `rsi_levels` / `lwc_chart`）。
- CSV 必須列（open/high/low/close）欠落・時刻列欠落: `KeyError`（`load_ohlc_csv`）。
- CSV ファイル不在: `FileNotFoundError`（`load_ohlc_csv`）。
- OHLC 長不一致: `ValueError`（`compute_rsi_full`）。
- `rsi_period < 2`: `ValueError`（`compute_rsi`。元 OnInit の `INIT_FAILED` に対応）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（`lwc_chart`）。

## 9. 元 MQL からの差分

### 一致を保証する点（原挙動の 1:1 再現）
- iRSI 算式（権威 Wilder = MetaQuotes 公式 `RSI.mq5`。seed=period 本平均, main=Wilder
  平滑 `(prev*(period-1)+x)/period`）。
- **ゼロ割の場合分け**: neg!=0→`100-100/(1+pos/neg)` / neg==0&pos!=0→100 /
  **neg==0&pos==0（flat window）→50** を 1:1 再現する。**flat→50 は MFI の flat→100
  とは異なる**（指標固有差）。
- **warm-up（`i<period`）は 0**（元 iRSI / SetIndexDrawBegin 既定。NaN ではない）。
  `rates_total<=period` は全 0（元 RSI.mq5 早期 return）。
- 適用価格 7 種（Apply→PRICE_*）の選択。既定 Apply=5 → Typical。
- σ 7 水準（p1/p2/p3/m1/m2/m3 ＝ **生 RSI 系列**全長の `平均 ±{1,2,3}×母標準偏差`、
  mid50=50）。**統計に warm-up の 0 が混入する**点、**生 RSI 系列に掛ける**点
  （ExtRSIBuffer 引数）も元挙動どおり再現する。
- subwindow 範囲 [0,100]（元 indicator_minimum 0 / indicator_maximum 100）。
- `rsi_period < 2` で例外（元 OnInit の INIT_FAILED）。
- 短名 "RSI-{適用価格名} ({period})"（Apply 依存）。

### 中立記載（原挙動の 1:1 再現として保持し "改善" しない）
- **warm-up 0・σ 統計への 0 混入・σ を生 RSI に掛ける**は統計的に平均・σ を歪める効果が
  あるが、これは元 MQL の挙動そのものである。除外・補正は行わず**原挙動として 1:1 再現
  する**（良し悪しの判断を持ち込まない中立記載）。

### 意図的に変えた / 前提化した点（根拠）
1. **iRSI は権威 Wilder（MetaQuotes 公式 `RSI.mq5`）準拠**: 元 `iRSI` は MT4 組込で
   ソース非公開のため、権威実装 `RSI.mq5` の Wilder 平滑・ゼロ割場合分けを採用する。
   **flat window→50**（MFI の 100 と異なる）。bit-exact は MT4 実機 CSV が無いため非保証。
2. **applied_price は共有層を再利用**: 適用価格 7 種を共有 `common` から再利用し、
   in-package 再実装しない（重複排除）。元 `iMAOnArray(MODE_EMA)` の EMA 平滑線は
   移植対象外（§2）。
3. **iRSI は共有 `mql_builtins` へ集約済み・σ 統計のみ in-package**: iRSI は共有
   `mql_builtins.compute_rsi` を import 再公開して参照面を維持する。σ 統計
   （`compute_rsi_levels`）のみ本指標専用プリミティブとして `src/core.py` 内に閉じる。
4. **`int` 切り捨て・`NormalizeDouble` は持ち込まない**（ガイド §4.1）。元コードに整数化・
   丸めは無く、float 精度で実装。
5. **bit-exact 非保証**: 元 `iRSI` は MT4 組込であり、参照 CSV が無いため MT4 実機との
   厳密一致は保証しない。完全一致が必要な場合は MT4 出力 CSV による回帰固定が必要。
