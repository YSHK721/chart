# !!R-tgp.BTLM-Ind 移植仕様書

## 1. Objective（目的）
直近 `maxbars` 本の **Open 価格をバー番号（1..maxbars）に回帰**し、ベイズ木構造線形モデル
（btlm）の**予測平均**と**上下予測分位点（信用区間）**を価格チャートに重畳表示する。
価格のトレンド推定と不確実性帯（バンド）の可視化が目的。

## 2. Scope（範囲・対象外）
- 移植する: 計算（btlm 回帰）/ 描画（overlay: 平均実線 + 上下点線）/ 入力（CSV → OHLC）。
- 対象外: ブローカー接続（元 `CopyRates`/MT4 レート供給）、アラート（`Alert`）、
  R の非同期実行制御（`RExecuteAsync`/`RIsBusy`）、コメント表示（`Comment`）、
  トレンド開始の垂直線（元コードでコメントアウト済みの `TrendStart`/`partition`）、
  R 側プロット（tree.png / x.pdf 等、コメントアウト済み）。

## 3. 元 MQL 情報
- ファイル: `sample/MQL4/Indicators/!!R-tgp.BTLM-Ind.mq4`（Copyright 2011, Fai Software Corp.）。MQL4。
- バッファ本数: 3（`buf_mean`, `buf_q1`, `buf_q2`）。プロット 3。`indicator_chart_window`（overlay）。
- 描画: 全て色 MediumSlateBlue。`buf_mean`=DRAW_LINE 実線（width2）、`buf_q1`/`buf_q2`=DRAW_LINE +
  STYLE_DOT（点線）。
- input パラメータ:
  | 名前 | 型 | 既定 | 意味 |
  |---|---|---|---|
  | `maxbars` | int | 100 | 当てはめ・描画に用いる直近本数 |
  | `RDebugLevel` | int | 1 | R ブリッジのデバッグ出力レベル（移植対象外） |
  | `RTempDir` | string | "" | R 作業ディレクトリ接尾辞（移植対象外） |
- 時系列の向き: MQL4 標準（`Open[]`/`Time[]` は index0=最新）。`hist` を `rev` で昇順化して R へ。
- 使用する標準関数/外部: R `tgp::btlm`。中核 `model <- btlm(X=X, Z=hist, verb=0, BTE=c(2000,15000,2), R=1)`。
  入力 `X <- seq(1, maxbars)`、`Z = rev(hist)`（昇順 Open）。出力 `model$Zp.mean`/`Zp.q1`/`Zp.q2`。

## 4. Input（入力）
- 必須列: `open`（回帰対象）。loader は既定で OHLC を要求するが `price` 引数で対象列を選択。
- 時刻列: lwc 描画時のみ必要（明示 > `time` > `date` > DatetimeIndex の順で解決）。
- 前提: 行は時系列昇順（古い→新しい）。

## 5. Processing（計算定義）
1. 当てはめ窓 = 直近 `window = min(maxbars, 行数)` 本。
2. 説明変数 `X = 1, 2, …, window`、目的変数 `Z = 直近 window 本の open（昇順）`。
3. モデル当てはめ（`BtlmFitter.fit_predict`）:
   - **TgpBtlmFitter（忠実）**: R `tgp::btlm(X, Z, BTE=(2000,15000,2), R=1, verb=0, pred.n=TRUE)`。
     `Zp.mean`=予測平均、`Zp.q1`=5% 分位、`Zp.q2`=95% 分位。
   - **OlsBtlmFitter（参照・R 不要）**: 単一区分ベイズ線形回帰。Φ=[1,x]、β̂=OLS、
     予測分散 `s²(1+φ₀ᵀ(ΦᵀΦ)⁻¹φ₀)`、分位点は正規近似（`norm_ppf`）。
4. 分位点水準: `q_low`/`q_high`（既定 0.05/0.95）。`TgpBtlmFitter` で非既定値の場合は
   ネイティブ 90% 帯から `σ=(q95−q5)/(2·z₀.₉₅)` を推定し正規近似で再構成。
5. 丸め: 無し（float64）。`int()` 切り捨ては元コードに無いため該当なし（ガイド §4.1）。
6. 落とし穴対応: 時系列の向き（§4.3）→ 昇順で扱い `rev` 不要。EMPTY_VALUE（§4.5）→ 窓外 NaN。
   非同期足場（§6 描画関心の分離）→ 同期実行へ。確率的 MCMC のためビット一致は非保証（§4.4 注記）。

## 6. Entities / 成果物（出力データ）
- 成果物 DataFrame（元 index 引き継ぎ）:
  | 列 | 意味 |
  |---|---|
  | `btlm_mean` | 予測平均（`model$Zp.mean`） |
  | `btlm_q{lo}` | 下側分位点（既定 `btlm_q5` = `Zp.q1`） |
  | `btlm_q{hi}` | 上側分位点（既定 `btlm_q95` = `Zp.q2`） |
- 窓外（直近 window 本より前）の行は `NaN`（元 `EMPTY_VALUE`）。

## 7. Output（描画）
- overlay（価格と同一スケールに重畳）。
- 系統 → 線種: `btlm_mean`=実線（width2）、`btlm_q{lo}`/`btlm_q{hi}`=点線。色 MediumSlateBlue
  (`#7B68EE` / `rgba(123,104,238,1)`)。fill は lwc 未対応のため上下点線で表現（ガイド §6）。
- lwc: 多線のため `price_line=False, price_label=False`。値列名はライン名と一致。
- NaN は描画側で除外（matplotlib は自動途切れ、lwc は dropna）。

## 8. Exception（異常系）
- 価格列欠落 → `KeyError`。
- `q_low/q_high` が `0 < q_low < q_high < 1` を満たさない → `ValueError`。
- 空 DataFrame / 空系列 → `ValueError`。
- Fitter 返り値長が窓と不一致 → `ValueError`。
- lwc で時刻解決不能 → `KeyError`。
- `TgpBtlmFitter`: rpy2 未導入 → `ImportError`、R パッケージ tgp ロード不可 → `RuntimeError`。
- `OlsBtlmFitter`: 観測 3 点未満 → `ValueError`。

## 9. 元 MQL からの差分
- **意図的に変えた点**:
  - 非同期 R 連携（`RExecuteAsync`/`RIsBusy`/再起動リカバリ）を除去し同期バッチ化（計算結果不変）。
  - 分位点を `q_low/q_high` で可変化（既定は元と同じ 5/95%）。非既定は正規近似で再構成。
  - R 依存を Protocol 境界の外へ隔離し、R 不在でも動く numpy 参照実装を追加。
- **元と一致を保証する点**:
  - 当てはめ窓（直近 `maxbars`）、説明変数 `X=1..maxbars`、目的変数=Open 昇順、
    既定分位点 5/95%、3 系列（平均＋上下）の意味。
  - 確率的 MCMC のため、`TgpBtlmFitter` でもシード固定下での**統計的一致**であり、
    ビット単位一致は保証しない（btlm の性質）。
