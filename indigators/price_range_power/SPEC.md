# 価格帯別ブルベアレシオ（PriceRangePower）移植仕様書

## 1. Objective（目的）
OHLC を価格帯（級, interval 刻み）別に集計し、各バーの **始値→終値方向（陽線/陰線/同値）**
で分類した 4 系統のヒゲ幅（上ヒゲ・下ヒゲ・高安レンジ）の **±1σ/2σ/3σ 度数** と、その
価格帯内比率（= ブル/ベアレシオ）を算出する。安値帯に集まる「支持（ブル）勢力」と高値帯に
集まる「抵抗（ベア）勢力」を価格帯ごとに定量化することが目的。

## 2. Scope（範囲・対象外）
- 移植する: 計算（`TA.PriceRangePower`）/ 入力（CSV → OHLC）/ 描画（価格帯プロファイル：
  matplotlib、価格チャート重畳：lightweight-charts 水平線）。
- 対象外: Excel シート I/O（`inputData.getDataInCells` / `outputData.iDataWrite`）、
  表示書式（`displayFormatSet.DF_PricerangePower`）、UserForm（`PriceRangePower.frm`：
  ComboBox/TextBox の GUI）、TOTAL 行の集約表示。

## 3. 元 VBA 情報
- ファイル: `sample/VBA/TECNICAL_ANALYSIS.cls`（`Function PriceRangePower`, 172-417 行）。
  起動元 `sample/VBA/価格帯別ブルベアレシオ.bas` → UserForm `PriceRangePower.frm` →
  `TA.PriceRangePower(iPDA, Val(ComboBox1), Val(TextBox3), Val(TextBox2))`。
- 依存プリミティブ（`sample/VBA/OPERATION.cls`）:
  - `fun_OpeCHANGE(tsd,"OC")` = `opeChangeDay(Close, Open)` = **Close − Open**（陽線=正）。
  - `opeMIN(inL)` / `opeMAX(inH)` = 安値の最小 / 高値の最大（Empty 除外）。
  - `opeAverage(tsd)` = Empty を除外した `Σ/cnt`（**該当バーのみ平均**）。
  - `opeSTDEV(tsd)` = Empty を除外した `Sqr(Σ(x-avg)²/(cnt-1))`（**標本標準偏差**）。
- 入力パラメータ（UserForm → TA）:
  | 名前 | 由来 | 既定 | 意味 |
  |---|---|---|---|
  | `interval` | ComboBox1（0.1/0.01/0.001） | 0.1 | 価格帯（級）の刻み幅 |
  | `rangeFrom` | TextBox3 | 安値の最小値 | 価格帯の開始価格 |
  | `rangeTo` | TextBox2 | 高値の最大値 | 価格帯の終了価格 |
- 時系列の向き: 昇順（古い→新しい）。VBA は先頭に Empty 行（ヘッダ）を持つ TSD 形式。
- バッファ本数/描画: 元は overlay でなくシート表（価格帯 × 27 列）。本移植は価格帯
  プロファイル図＋価格チャート水平線で再表現。

## 4. Input（入力）
- 必須列: `open` / `high` / `low` / `close`（列名の大小不問）。
- 前提: 行は時系列昇順。NaN を含む行は統計・度数集計から除外される（元 Empty 相当）。
- 時刻列: 計算には不要。CSV ローダで任意指定可（index 化）。

## 5. Processing（計算定義）— 一意に
1. **方向分類**: `oc = close − open`。`oc>0` 陽線 / `oc<0` 陰線 / `oc==0` 同値。
2. **ヒゲ幅抽出**（非該当バーは NaN。元 VBA の Variant 配列 Empty を NaN で再現）:
   - `oc>0`: `hc = high−close`, `lh = high−low`
   - `oc<0`: `ol = open−low`, `hl = high−low`
   - `oc==0`: `hc = high−close`, `ol = open−low`
3. **系統別閾値**（系統 ∈ {hc, ol, hl, lh}。NaN 除外）:
   - `avg = mean(非NaN)`、`std = 標本標準偏差(ddof=1)`。該当 1 本以下は `std=NaN`。
   - `a1 = avg+std`, `a2 = avg+2std`, `a3 = avg+3std`。
4. **価格帯生成**: `band[0]=range_from`、`直前<=range_to の間` `band[k]=RoundUp(band[k-1]+interval, 4)`。
   ⇒ 最後の 1 本は `range_to` を初めて超える（元境界挙動）。`RoundUp` は 0 から遠ざかる切り上げ。
5. **度数集計**（各帯 `[band, band+interval)`、各バー）:
   - `low ∈ 帯` → `fda_f_l += 1`、`ol`/`lh` を σ ビン分類して加算。
   - `high ∈ 帯` → `fda_f_h += 1`、`hc`/`hl` を σ ビン分類して加算。
   - σ ビン（先勝ち）: `[a1,a2]→1` / `(a2,a3]→2` / `>a3→3` / それ以外（NaN・a1未満）→ 非加算。
6. **比率**: ブル `f_ol_*%/fda_f_l`, `f_lh_*%/fda_f_l`、ベア `f_hc_*%/fda_f_h`, `f_hl_*%/fda_f_h`。
   **分母または分子が 0 のとき NaN**（元 Empty。0 ではない）。
7. **合計**: `total = 比率12列の行和`（NaN は 0 とみなす。元 col27 TOTAL）。
8. 落とし穴対応:
   - §4.1 `RoundUp` の FP ノイズ（1.1+0.1=1.2000…2）を 6 桁丸めで安定化。`int()` 切り捨ては元になく非該当。
   - §4.4 非対称分類を忠実再現（陽線→hc/lh、陰線→ol/hl、同値→hc/ol。高値帯では陽線が hc・
     陰線が hl に寄与する非対称）。
   - §4.5 非該当・空帯は NaN（描画側で除外）。

## 6. Entities / 成果物（出力データ）
- 成果物 DataFrame: index = `prp`（バンド下端）、列 = 度数14 + 比率12 + `total`。
  | 系統 | 度数列 | 比率列 |
  |---|---|---|
  | 価格帯度数（安値/高値） | `fda_f_l` / `fda_f_h` | — |
  | 下ヒゲ OL（ブル, 安値帯） | `f_ol_a1..a3` | `f_ol_a1_pct..a3_pct` |
  | 安→高 LH（ブル, 安値帯） | `f_lh_a1..a3` | `f_lh_a1_pct..a3_pct` |
  | 上ヒゲ HC（ベア, 高値帯） | `f_hc_a1..a3` | `f_hc_a1_pct..a3_pct` |
  | 高→安 HL（ベア, 高値帯） | `f_hl_a1..a3` | `f_hl_a1_pct..a3_pct` |
  | 合計 | — | `total` |
- 要約 DataFrame（`build_bull_bear_profile`）: `freq_low`/`freq_high`/`bull_power`/
  `bear_power`/`net_power`(=bull−bear)/`total`。
- 比率の Empty → NaN。

## 7. Output（描画）
- matplotlib（`plot_price_range_power`）: 価格帯プロファイル。左=度数（安値◀/▶高値）、
  右=ブル勢力（緑, 右）/ベア勢力（赤, 左）。ヘッドレス（Agg）。
- lightweight-charts（`add_price_range_power`）: 価格チャートに、ブル優位帯（緑）/ベア優位帯
  （赤）の上位 `top_n` 本を **水平価格ライン**で重畳（duck typing: `horizontal_line`）。勢力 0 は非描画。

## 8. Exception（異常系）
- OHLC 列欠落 → `KeyError`（`build_price_range_power`）。
- OHLC 長不一致 / 空 → `ValueError`（`wick_samples`）。
- `interval<=0` → `ValueError`（`build_price_bands`）。
- `top_n<0` → `ValueError`（`add_price_range_power`）。
- CSV 不在 / 必須列欠落 / 時刻列欠落 → `FileNotFoundError` / `KeyError`（`load_ohlc_csv`）。

## 9. 元 VBA からの差分
- **一致を保証**: 方向分類（陽線/陰線/同値の非対称な系統割当）、`opeAverage`（該当バーのみ平均）、
  `opeSTDEV`（標本標準偏差 ÷(n−1)）、σ ビンの境界（先勝ち `[a1,a2]/(a2,a3]/>a3`）、
  比率の Empty 条件（分母/分子 0 → NaN）、バンド生成の境界（最後 1 本が range_to 超）。
  独立リファレンス（`tests/test_core.py` の `_ref_prp`：VBA 擬似コード 1:1 転記）と一致を固定。
- **意図的に変更**:
  - `WorksheetFunction.RoundUp` の FP ノイズを 6 桁丸めで安定化（実装都合の桁あふれ除去。ガイド §4.1）。
  - 元 resPRP は `((H−L)/iv)+2` 行で確保し末尾に空（級=0）行が残るが、これは実装都合の
    アーティファクトのため再現せず、意味のある帯のみ生成する（ガイド §4.1）。
  - Excel シート出力・表示書式・UserForm GUI は対象外（§2）。
