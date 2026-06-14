# 上流入力前提検証（upstream-input-validation）— profit_rmm_macd 因果窓化 TDD

## 上流入力の整理（step S-1）

| 種別 | 件数 | 内容 |
|---|---|---|
| 依頼者指示 | 1 件 | TDD（Red→Green）で「標準化スパンの因果ローリング窓化」を実装せよ（window 貫通・EMA非汚染対処・実データ検証） |
| 他者レビュー指摘 | 0 件 | 該当なし |
| 前段成果物 | 2 件 | ①「アーキ評価の結論」＝EMA連鎖が level_count を入力にし共有EMAがNaN汚染する。②「rmm_macd には σ水準関数は無いとアーキ評価で確認済み」 |
| 既存合意の引き継ぎ | 1 件 | 「忠実移植は放棄済み（元 MQL 全期間挙動からの乖離は承認済み）」 |

## 前提抽出（step S-2）

| # | 上流主張（要約） | 暗黙の前提 | 独立検証可能性 |
|---|---|---|---|
| P1 | 共有EMAは種 buffer[0]=price[0] かつ NaN ガード無し→warm-up NaN で全期間 NaN 汚染 | exponential_ma_on_buffer が buffer[begin]=price[begin]、以降 price[i]*sf+buffer[i-1]*(1-sf) で NaN 伝播 | 可（Read） |
| P2 | rmm_macd に σ水準関数は無い | core.py に compute_*_levels / lc_levels が存在しない | 可（grep） |
| P3 | sister profit_rmm の rolling_span / DEFAULT_WINDOW=120 をコピー流用可 | sister core.py に当該実装が実在 | 可（Read 済） |
| P4 | CSV に volume 列が無い | jp225_daily.csv ヘッダに volume が無い | 可（head） |
| P5 | level_count 全NaN時 start を末尾相当にする必要 | np.argmax(np.isfinite(all_nan)) が 0 を返し誤判定 | 可（python 実行） |

## 証拠先行検証（step S-3）

| # | 実証手段 | 出力 | 判定 |
|---|---|---|---|
| P1 | Read moving_averages/src/core.py:212-223 | `buffer[begin]=price[begin]`、L218/223 で NaN ガード無く `price[i]*sf+buffer[i-1]*(1-sf)`。price[0]=NaN→全要素 NaN | 実証取得（汚染成立） |
| P2 | grep core.py | levels コメントのみ、関数・dict 出力なし。既存 test が `not hasattr(core,"compute_rmmmacd_levels")` で固定済 | 実証取得（σ水準無し） |
| P3 | Read profit_rmm/src/core.py:70,118-151 | `DEFAULT_WINDOW=120`、`rolling_span(x,window,*,clamp)` 実在（cumsum 方式・warm-up NaN） | 実証取得 |
| P4 | head jp225_daily.csv | `date,open,high,low,close`（volume 無し）、1379 データ行 | 実証取得 |
| P5 | python3 np.argmax(np.isfinite(全NaN)) | `0` を返す。全NaN時 start=0→[0:0] で全列 EMA 対象になり汚染再発 | 実証取得（明示分岐必須） |

## 判定結果（step S-4）

| 上流入力 | 判定 | 根拠 |
|---|---|---|
| 依頼者指示（TDD 因果窓化） | 採用 | P3 で sister パターン実在を実証 |
| 前段成果物①（EMA NaN 汚染） | 採用 | P1 で buffer[begin]=price[begin]・NaN ガード無しを実コードで実証 |
| 前段成果物②（σ水準無し） | 採用 | P2 で関数不在を実証 |
| 既存合意（忠実移植放棄・乖離承認済み） | 条件付き採用 | 乖離承認は依頼文宣言で第三者検証不能。ただし window=None 経路の数学的同一性は既存 test で独立固定して採用 |
| 全NaN時の start 末尾化 | 採用＋強化 | P5 で argmax の 0 返し誤判定を実証。全NaN→全出力NaN をテストで固定 |

## 残存リスク（step S-5）

- jp225 の volume 補完値 1000.0 は依頼指定値。MFI への影響は数値検証時に観測のみ（仕様妥当性は範囲外）。
- 「乖離承認済み」は依頼文宣言に依拠し承認ログ未確認。後続レビューフェーズに委ねる。
