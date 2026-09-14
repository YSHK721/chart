# PRO!fit_OSI_MA 移植仕様書

## 1. Objective（目的）
終値の移動平均（MA）からの**乖離率 MAKairi（%）**を別ウィンドウのヒストグラムで
可視化する。価格が MA に対しどれだけ上下に離れているか（オシレーター的な行き過ぎ／
戻りの度合い）を、0 を基準とした符号付きの棒で表現する。

## 2. Scope（範囲・対象外）
- 移植する: 計算（MA → 乖離率 MAKairi）/ 描画（separate window のヒストグラム +
  水準線 1/0.5/-0.5/-1）/ 入力（CSV → OHLC）。
- 対象外: ブローカー接続・チャートデータ供給（`Close[]` の供給）、アラート、最適化
  入力、リアルタイム差分再計算（`IndicatorCounted` による増分更新はバッチ全件計算で
  代替。ガイド §3/§6）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/PRO!fit_OSI_MA.mq4`（Copyright 2015, PRO!fit
  System Investars, version 1.00, `#property strict`）。
- 種別: MQL4。`#property indicator_separate_window`、`indicator_buffers 1`。
  バッファ `MAKairi`（`SetIndexStyle(0, DRAW_HISTOGRAM)`,
  `#property indicator_color1 Red`, `SetIndexLabel(0, "MAKairi")`）。
- 水準線: `#property indicator_level1 1` / `indicator_level2 0.5` /
  `indicator_level3 -0.5` / `indicator_level4 -1`（計 4 本、±1.0 / ±0.5）。
- input パラメータ:
  - `extern int MAMode = 1`（0=SMA, 1=EMA, 2=SMMA, 3=LWMA。既定 EMA）。
  - `extern int MAPeriod = 21`（MA 期間。既定 21）。
- 時系列の向き: `ArraySetAsSeries` 明示なし＝MQL4 既定の **series（index 0 = 最新足）**。
  `start()` は `i` 昇順（0..limit-1, i=0 が最新）で `MAKairi[i]` を埋める。本移植は
  **昇順（古い→新しい）**で扱い、series 添字 `Close[i+1]`（1 本古い終値）を昇順では
  `close[a-1]` に変換する（ガイド §4.3/§4.4）。
- 使用する標準/ライブラリ関数:
  - `iMA(NULL, 0, MAPeriod, 0, MAMode, PRICE_CLOSE, i)` … 終値 MA 本線。MAMode で
    SMA/EMA/SMMA/LWMA を切替。本移植は共有ライブラリ `moving_averages`
    （`MovingAverages.mqh` 移植）の `*_ma_on_buffer` 系で再現する。

## 4. Input（入力）
- 必須列: `close`（列名の大小不問）。CSV ローダ（`load_ohlc_csv`）は元 MQL の
  OHLC 供給に倣い `open/high/low/close` を必須とする（計算は close のみ使用）。
- 前提: 行は時系列昇順。

## 5. Processing（計算定義）— 一意に

### 5.1 MA バッファ（`compute_osi_ma` 内部, MAMode/MAPeriod）
昇順 close に対し、MAMode に応じた MA を共有 `moving_averages` の on_buffer で算出する:

| MAMode | MA 種別 | 関数（共有 moving_averages） |
|---|---|---|
| 0 | SMA  | `simple_ma_on_buffer` |
| 1 | EMA  | `exponential_ma_on_buffer`（既定） |
| 2 | SMMA | `smoothed_ma_on_buffer` |
| 3 | LWMA | `linear_weighted_ma_on_buffer` |

MA 未確定区間（SMA/SMMA/LWMA の warm-up）は on_buffer 出力が `0.0` となる。

### 5.2 乖離率 MAKairi（一意定義）
元 MQL（series, index0=最新）::

    ma = iMA(NULL,0,MAPeriod,0,MAMode,PRICE_CLOSE,i);
    if (ma != 0) MAKairi[i] = (Close[i+1] - ma) / ma * 100;

昇順（古→新, a = バー添字）へ変換した一意定義（実装）::

    kairi[a] = (close[a-1] - ma_a) / ma_a * 100

- **分子は `close[a]` ではなく `close[a-1]`（1 本古い終値）**。元の `Close[i+1]`
  （series で 1 本古い足）の非対称性を昇順で 1:1 再現したもの（§9・ガイド §4.4）。
- NaN（非描画）条件:
  1. `a == 0`（最古バー。`close[a-1]` が不在）。
  2. `ma_a == 0.0`（元 `if (ma != 0)` のゼロ除算ガード。MA 未確定区間を含む）。
  3. `ma_a` が NaN（MA 未確定）。

### 5.3 水準線（`osi_ma_levels`）
元 `#property indicator_level1..4` に対応するスカラ参照値（時系列ではない）:

    {"lvl_1": 1.0, "lvl_05": 0.5, "lvl_-05": -0.5, "lvl_-1": -1.0}

### 5.4 丸め・補間方式
- 元 MQL に `NormalizeDouble` は無く、乖離率に丸めを持ち込まない（float 精度。ガイド §4.1）。
- MA は共有 `moving_averages` の方式（EMA は α=2/(N+1) 等）に従う。

## 6. Entities / 成果物（出力データ）
`build_osi_ma` の DataFrame（index=入力 index 継承）:

| 列 | 意味 |
|---|---|
| `osi_ma_kairi`（`KAIRI_COLUMN`） | MA からの乖離率 MAKairi（%）。符号付き。NaN を保持。 |

水準線は `osi_ma_levels()` でスカラ提供（時系列ではないため成果物 DataFrame と分離）。
EMPTY_VALUE 相当の非描画点は §5.2 の NaN 条件に該当するバーで発生し、描画側
（matplotlib の `bar()` / lightweight-charts の `dropna`）が自然に欠落させる。

## 7. Output（描画）
- 別ウィンドウ（separate window）型。
- matplotlib（`plot_osi_ma`）: 下段ペインに棒ヒストグラム（Red, 0 基準線あり）+
  水準線 4 本（1/0.5/-0.5/-1, 点線）。NaN は `bar()` が描画しない。
- lightweight-charts（`add_osi_ma`, duck typing）: `create_histogram`（名前
  `osi_ma_kairi`, Red, price_line/label=False）に dropna 後の time＋値を `set` し、
  水準線 4 本を `horizontal_line`（点線）で追加。`draw_levels=False` で水準線を抑制可能。

## 8. Exception（異常系）
- close 列欠落: `KeyError`（`build_osi_ma` / `lwc_chart.add_osi_ma`）。
- CSV 必須列欠落（open/high/low/close）: `KeyError`（`load_ohlc_csv`）。
- CSV 不在: `FileNotFoundError`（`load_ohlc_csv`）。
- `ma_mode` が 0..3 外: `ValueError`（`compute_osi_ma`）。
- `ma_period <= 0`: `ValueError`（`compute_osi_ma`）。
- `ma_period == 1`: 例外なし・**全 NaN**（共有 `moving_averages` の on_buffer 関数が
  `period <= 1` で未計算 0 を返し、MA 未確定として NaN 化されるため。元 MQL の
  `iMA(period=1)`＝価格そのもの、とは異なる未対応域。挙動固定）。
- 時刻列（time/date/DatetimeIndex）解決不可: `KeyError`（`lwc_chart`）。
- 空 close: 空配列を返す（例外なし。挙動固定）。

## 9. 元 MQL からの差分

### 一致を保証する点
- 乖離率の算式 `kairi = (close[a-1] - ma)/ma*100`（**1 本ずれを含む**）、
  ゼロ除算ガード（`ma != 0`）、MAMode→MA 種別の対応、既定 MAMode=1(EMA)/MAPeriod=21。
- 水準線 4 本（1 / 0.5 / -0.5 / -1）。separate window のヒストグラム 1 本（色 Red）。
- MA は共有 `moving_averages`（`MovingAverages.mqh` 移植）の on_buffer に基づく
  （未確定区間 = 0.0、EMA の seed 等は同ライブラリの方式に従う）。

### 意図的に変えた / 前提化した点（根拠）
1. **`Close[i+1]` の 1 本ずれ（最重要）**: 元コードは MA を足 `i` で算出する一方、
   分子に `Close[i+1]`（series で **1 本古い**終値）を用いており、MA と終値の足が
   1 本ずれる非対称性がある。本移植はこれを**原挙動として未修正のまま 1:1 再現**し、
   昇順では `kairi[a] = (close[a-1] - ma_a)/ma_a*100` とする。**これがバグ（`Close[i]`
   の書き間違い）か意図的設計かは元コードからは断定できない**。本移植の目的は元
   インジケーターの出力を忠実に再現することであり、是非を判断せず再現を保証する。
   「改善」して `close[a]` に変更することは行わない（ガイド §4.4 非対称性の忠実再現）。
   - 該当再現の固定: `tests/test_core.py::test_numerator_uses_close_a_minus_1_not_close_a`
     が `close[a-1]`（正）と `close[a]`（誤実装）で値が分岐する入力で 1:1 を固定する。
2. **丸めを持ち込まない**: 元に `NormalizeDouble` は無く、float 精度で乖離率を算出する
   （ガイド §4.1）。
3. **時系列の向きの変換**: 元は series（index0=最新）。本移植は昇順（古→新）で計算し、
   series 添字 `Close[i+1]` を `close[a-1]` に対応付ける（ガイド §4.3）。出力値は同一。
4. **ビット完全一致は非保証**: MT4 実機の参照 CSV（`iMA` 出力 / MAKairi バッファ）が
   無いため、MA の warm-up・seed 区間は実機と厳密一致しない可能性がある。完全一致が
   必要な場合は参照 CSV による回帰固定が必要。
