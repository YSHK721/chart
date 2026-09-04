# PRO!fitRSI 移植仕様書

## 1. Objective（目的）
適用価格（既定 Typical price）に対する相対力指数（Relative Strength Index, RSI,
既定 period=6）を別ウィンドウ（[0,100]）の RSI 線で可視化し、**その時点までの RSI 分布**から
「普段の範囲（正常帯）」と「過熱の極端さ（外れ値水準）」を因果的に推定して重ねる。
上昇幅と下降幅の Wilder 平滑比から相場の過熱・冷却の相対強度を表現し、水準はそれが
**普段と比べてどれほど異常か**を与える。

## 2. Scope（範囲・対象外）
- 移植する: 計算（iRSI）/ 描画（separate window [0,100] の RSI 線 ＋ 正常帯 2 本 ＋
  外れ値水準 4 本）/ 入力（CSV → OHLC、**volume 不要**）。
- 対象外: **RSI の EMA 平滑線（元 `ExtMABuffer` / `InpMAPeriod`）**。描画専用で他の出力に
  影響しないため、設定項目 `ma_period` ごと削除した（ユーザー承認 2026-08-02）。
- 対象外: **元の σ 7 水準（全系列 avg±1/2/3σ ＋ 固定 50）**。全系列＝**未来のバーを含む
  非因果な水準**であり、ライブ表示の水準として成立しない。因果ローリング分位＋POT/GPD
  （§5.4）へ全面置換した（ユーザー承認 2026-08-02）。
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

### 5.4 正常帯（因果ローリング分位）と外れ値水準（POT / GPD）
実装は `src/levels.py`。共有プリミティブ（`common.marod_bands` / `common.event_quantiles` /
`common.gpd`）を**無改変で参照**し、計算式を写さない。tickvol（`indigators/tickvol/src/levels.py`）
と同型の構造で、**超過の測り方だけが RSI 固有**である。

1. **正常帯＝POT の閾値** `u_t`: 当該バー除外の因果ローリング分位（窓 `window_n`・下側
   `q_low` / 上側 `q_high`）。非リペイント。
2. **超過は「余地割合」で測る（RSI 固有）**:

   ```
   上側: excess = (RSI − u_hi) / (100 − u_hi)      下側: excess = (u_lo − RSI) / u_lo
   ```

   RSI は [0,100] の有界量である。tickvol と同じ生スケール（`RSI − u`）だと「現在の閾値
   ＋ 過去の超過量」が境界を越え、**実測で全バーの 26〜35% が [0,100] の外**へ出た。
   余地割合なら値域が (0,1] で、水準 `u ± 割合 × 余地` は**構成上境界を出ない**。
3. **エピソード畳み込み（宣言クラスタリング）**: 超過が続く区間を 1 エピソードへ畳み、その
   極値を 1 観測とする（`common.event_quantiles.step_events` の `event_agg="episode"`）。
4. **水準（同じ観測集合・同じ分位を 2 通りで推定）**: 直近 `k_events` 件から
   経験的分位（`common.event_quantiles.levels_at`）と GPD 外挿
   （`common.gpd.gpd_excess_quantile`）。2 本の差が外挿量そのものになる。
   GPD は観測 30 件（`common.gpd.MIN_GPD_EVENTS`）未満では出さない（NaN＝非描画）。
   余地割合の台は 1.0 なので、外挿値は 1.0 で頭打ちにする。

**実測の根拠（2026-08-02・jp225_tick・5m/15m/1h/4h/1D × 上下側）**:

| 測定 | 結果 |
|---|---|
| θ̂（生の閾値超過） | 0.206〜0.295（ISSUE-227 の RSI 系列 θ̂ = 0.107〜0.269 と整合） |
| θ̂（エピソード畳み込み後） | **0.859〜0.947**（ゲート θ >= 0.2 を通過） |
| 観測数（エピソード） | 95〜1,625 件（GPD 最小 30 を全条件で満たす） |
| ξ̂ | 全条件で負（−0.20〜−1.08）＝有限終端 |
| GPD 終端 vs 理論境界 | 上側 94.9〜107.0（境界 100）／下側 −3.2〜+7.4（境界 0） |
| AD 適合度（直近 k 件） | p = 0.475〜0.960（全条件で非棄却） |
| AD 適合度（全履歴） | 1h 下側 p=0.005・4h 下側 p=0.020 で棄却＝**ローリング必須** |
| 水準が [0,100] の外 | 生スケール 26〜35% → 余地割合 0〜0.7%（台 1.0 で抑えて 0%） |

閾値分位の既定（`q_high=0.90` / `q_low=0.10`）は、ForwardStop（`common.gpd.select_threshold`・
全履歴）の採択が時間足ごとに 0.80〜0.95 へ散る一方、運用と同じ直近 `k_events` 件の当てはめでは
0.80〜0.95 のいずれでも棄却率が名目 5% と整合する（窓 10 本で 0〜20%）ため、**観測数を最も
確保できる中央値**として選んだ（tickvol と同値）。

### 5.5 丸め・補間方式
- 元コードに `NormalizeDouble` は無く、float 精度で実装（ガイド §4.1, int 切り捨ても
  持ち込まない）。
- 標準偏差は母分散ベース（÷N, MT4 iStdDev 準拠）。

## 6. Entities / 成果物（出力データ）
`build_rsi` の DataFrame（index=入力 index）:
| 列 | 意味 |
|---|---|
| `rsi`（`RSI_COLUMN`） | iRSI 値（描画対象, 線）。warm-up は 0。 |
| `rsi_q{pct}` × 2 | 正常帯（因果ローリング分位＝POT 閾値）。列名は分位から導く（`quantile_column`）。 |
| `rsi_evq_ext_hi` / `_lo` | 経験的極端分位の水準（`LEVEL_COLUMNS`）。 |
| `rsi_gpd_hi` / `_lo` | GPD 外挿の水準（同上）。 |

水準は**時系列**（価格軸の固定水平線ではない）。因果ローリング分位に基づき時間で動くため、
成果物 DataFrame の列として提供し、描画も line で出す。warm-up・観測不足の区間は NaN＝
非描画（RSI 本線は元どおり warm-up も 0 で全バー算出）。

## 7. Output（描画）
- 別ウィンドウ（separate window）型。y 範囲 [0,100]（元 indicator_minimum 0 /
  indicator_maximum 100）。
- RSI 線の凡例: 元 `IndicatorShortName` "RSI-{適用価格名} ({period})"（Apply で適用
  価格名が変わる。例 Apply=5 → "RSI-Typical price (6)"。`plot.rsi_short_name`）。
- matplotlib: 下段ペインに **RSI 線**（Lime）＋ 正常帯 2 本（点線シアン）＋ 外れ値水準 4 本
  （経験的＝赤系破線・GPD＝琥珀破線）。
- lightweight-charts: `create_line` 7 本（`rsi` / `rsi_q{low}` / `rsi_q{high}` /
  `rsi_evq_ext_hi` / `rsi_evq_ext_lo` / `rsi_gpd_hi` / `rsi_gpd_lo`）。**水平線は使わない**
  （水準が時間で動くため）。色・線種は共有規約（正常帯＝シアン点線 / 経験的＝`EVQ_COLOR`
  破線 / GPD＝琥珀破線）に従い、共有定数は書き換えない。多数線のため price_line/label=False
  （ガイド §6）。ライン名は値列名と完全一致（ガイド §5。Apply 依存の短名は lwc line name
  には用いず plot 凡例に限定）。`lightweight_charts` は import せず duck typing で受ける。

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
- subwindow 範囲 [0,100]（元 indicator_minimum 0 / indicator_maximum 100）。
- `rsi_period < 2` で例外（元 OnInit の INIT_FAILED）。
- 短名 "RSI-{適用価格名} ({period})"（Apply 依存）。

### 中立記載（原挙動の 1:1 再現として保持し "改善" しない）
- **warm-up 0** は統計的に不自然だが元 MQL の挙動そのものである。RSI 本線では除外・補正を
  行わず**原挙動として 1:1 再現する**（良し悪しの判断を持ち込まない中立記載）。なお §5.4 の
  水準は当該バー除外の因果ローリング分位を使うため、warm-up 区間は帯が NaN＝非描画になる。

### 意図的に変えた / 前提化した点（根拠）
1. **iRSI は権威 Wilder（MetaQuotes 公式 `RSI.mq5`）準拠**: 元 `iRSI` は MT4 組込で
   ソース非公開のため、権威実装 `RSI.mq5` の Wilder 平滑・ゼロ割場合分けを採用する。
   **flat window→50**（MFI の 100 と異なる）。bit-exact は MT4 実機 CSV が無いため非保証。
2. **applied_price は共有層を再利用**: 適用価格 7 種を共有 `common` から再利用し、
   in-package 再実装しない（重複排除）。元 `iMAOnArray(MODE_EMA)` の EMA 平滑線は
   移植対象外（§2）。
6. **σ 7 水準 → 因果ローリング分位＋POT/GPD へ全面置換**（§5.4・承認 2026-08-02）。
   元の水準は `iStdDevOnArray(ExtRSIBuffer, 0, rates_total, ...)`＝**全系列**の統計であり、
   バー t の水準が t より後のバーに依存する（非因果・リペイント）。ライブ／リプレイの
   水準として成立しないため、当該バー除外の因果ローリング分位を閾値とする POT へ置換した。
   水準の推定量は経験的分位と GPD 外挿の 2 本で、いずれも共有プリミティブへ委譲する。
3. **iRSI は共有 `mql_builtins` へ集約済み・σ 統計のみ in-package**: iRSI は共有
   `mql_builtins.compute_rsi` を import 再公開して参照面を維持する。σ 統計
   （`compute_rsi_levels`）のみ本指標専用プリミティブとして `src/core.py` 内に閉じる。
4. **`int` 切り捨て・`NormalizeDouble` は持ち込まない**（ガイド §4.1）。元コードに整数化・
   丸めは無く、float 精度で実装。
5. **bit-exact 非保証**: 元 `iRSI` は MT4 組込であり、参照 CSV が無いため MT4 実機との
   厳密一致は保証しない。完全一致が必要な場合は MT4 出力 CSV による回帰固定が必要。
