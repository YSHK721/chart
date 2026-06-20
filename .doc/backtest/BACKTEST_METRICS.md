# バックテスト分析結果 用語辞典（MT5 Strategy Tester）

MetaTrader 5 のストラテジーテスターがバックテスト終了後に表示する分析結果（「設定」「結果」「グラフ」「バックテスト」タブおよびレポート HTML）に出現する専門用語を、**算出式・MQL5 側の参照定数・解釈上の注意**まで含めて網羅する。

- **対象**: MetaTrader 5（build 4000 系以降）のストラテジーテスター出力。
- **目的**: Python 等で再実装したバックテスト結果と MT5 出力を 1:1 で突き合わせるための共通語彙を確定する。
- **記述方針**: 公式ドキュメント（`TesterStatistics()` の `ENUM_STATISTICS`）に存在する識別子は併記する。存在しないものは「(計算値)」と明示する。
- **通貨単位**: 口座通貨。`points`/`pips` 表記は明示する。
- **関連ドキュメント**:
  - 戦略ロジック仕様: [`./BACKTEST_SPEC.md`](./BACKTEST_SPEC.md)
  - 実行プロセス（OnTick の処理順）: [`./BACKTEST_PROCESS.md`](./BACKTEST_PROCESS.md)
  - Python 設計仕様（9 項目）: [`./BACKTEST_DESIGN.md`](./BACKTEST_DESIGN.md)
  - 指標計算モデル: [`./INDICATOR_CALC_MODEL.md`](./INDICATOR_CALC_MODEL.md)
  - MQL5 → Python 移植ガイド: [`./PORTING_GUIDE.md`](./PORTING_GUIDE.md)

---

## 0. 結果タブの構成（どこに何が出るか）

| タブ／領域 | 主な内容 |
|---|---|
| **設定（Settings）** | EA 名・パラメータ・期間・初期残高・通貨・レバレッジ・ティック生成方式 |
| **結果（Backtest results）** | §1〜§5 のサマリー数値（純益・PF・DD・連勝記録等） |
| **グラフ（Graph）** | バランス曲線・有効証拠金曲線・線形回帰線（LR balance）・ラウンド単位の損益バー |
| **バックテスト（Backtest）** | 発注・約定の明細（時刻・タイプ・サイズ・価格・SL/TP・損益・残高・コメント） |
| **最適化結果（Optimization Results）** | 最適化時のパス×指標マトリクス（純益/PF/Sharpe/Custom 等） |
| **最適化グラフ（Optimization Graph）** | 1D/2D/3D の指標分布図 |
| **フォワード結果（Forward Results）** | フォワードテスト区間の独立サマリー |
| **ジャーナル（Journal）** | OnInit/OnTick/Print の実行ログ・エラー |

---

## 0.5 記号定義（共通）

以下、特に断りのない限り次の記号を用いる。

| 記号 | 定義 |
|---|---|
| `N` | 確定トレード総数（=Total Trades） |
| `p_i` | i 番目のトレード確定損益（スワップ・手数料込み、口座通貨） |
| `W = { p_i \| p_i > 0 }` | 勝ちトレード集合 |
| `L = { p_i \| p_i < 0 }` | 負けトレード集合 |
| `N_w = \|W\|, N_l = \|L\|` | 勝ち数・負け数 |
| `B_0` | 初期残高（Initial Deposit） |
| `B_k = B_0 + Σ_{i≤k} p_i` | k 番目トレード確定後のバランス |
| `E_t` | 時刻 t における Equity（未決済含み損益を含む） |
| `HPR_i = (B_i + p_i) / B_i = B_{i+1} / B_i` | i 番目トレードの HPR（無リスク金利 = 0） |

---

## 1. 損益サマリー系

口座全体の収益性を 1 行で示す代表値群。

### 1.1 一覧

| 用語（日 / 英） | 算出式 | `ENUM_STATISTICS` 定数 | 補足 |
|---|---|---|---|
| **総純益 / Total Net Profit** | `Σ p_i` | `STAT_PROFIT` | スワップ・手数料込み |
| **総利益 / Gross Profit** | `Σ_{p_i>0} p_i` | `STAT_GROSS_PROFIT` | ≥ 0 |
| **総損失 / Gross Loss** | `Σ_{p_i<0} p_i` | `STAT_GROSS_LOSS` | ≤ 0（符号は負） |
| **プロフィットファクター / Profit Factor** | `GrossProfit / \|GrossLoss\|` | `STAT_PROFIT_FACTOR` | `GrossLoss = 0` のとき MT5 は表示上 ∞（`DBL_MAX` 相当） |
| **リカバリーファクター / Recovery Factor** | `\|TotalNetProfit\| / EquityDD_abs` | `STAT_RECOVERY_FACTOR` | DD 耐性。DD = 0 なら ∞ |
| **期待利得 / Expected Payoff** | `TotalNetProfit / N` | `STAT_EXPECTED_PAYOFF` | 1 トレード平均損益 |
| **シャープレシオ / Sharpe Ratio** | `(mean(HPR) − 1) / σ(HPR)` | `STAT_SHARPE_RATIO` | 詳細式は §1.2 |
| **AHPR / Average HPR** | `(1/N)·Σ HPR_i` | (計算値) | 算術平均保有期間収益率 |
| **GHPR / Geometric HPR** | `(Π HPR_i)^(1/N)` | (計算値) | 幾何平均保有期間収益率 |
| **LR Correlation** | §1.3 | (計算値・`ENUM_STATISTICS` に該当なし) | −1 〜 +1。1.0 に近いほど直線的に増加 |
| **LR Standard Error** | §1.3 | (計算値・`ENUM_STATISTICS` に該当なし) | 残高変動の散らばり（口座通貨） |

### 1.2 シャープレシオ詳細式

MT5 仕様（無リスク金利 = 0）:

```
mean_HPR = (1/N) · Σ_{i=1..N} HPR_i
var_HPR  = (1/N) · Σ_{i=1..N} (HPR_i − mean_HPR)^2          ← 母分散（n で除算、(n−1) ではない）
σ_HPR    = sqrt(var_HPR)
Sharpe   = (mean_HPR − 1) / σ_HPR
```

- 分子は `mean(HPR) − 1`（＝平均リターン）。MT5 公式 `(AHPR − 1) / σ`。
- 分散は **母分散**（n で除算）。`(n−1)` のサンプル分散ではない。
- `σ_HPR = 0` のとき MT5 は 0 を返す（ゼロ除算は回避される）。

### 1.3 線形回帰系（LR Correlation / LR Standard Error）

バランス系列 `(x_i, B_i)`（`x_i = i`, `i = 0..N`）に最小二乗線形回帰を適用:

```
B̂_i = a + b · x_i
b    = Σ(x_i − x̄)(B_i − B̄) / Σ(x_i − x̄)^2
a    = B̄ − b · x̄
```

- **LR Correlation**:
  ```
  r = Σ(x_i − x̄)(B_i − B̄) / sqrt(Σ(x_i − x̄)^2 · Σ(B_i − B̄)^2)
  ```
  値域 [−1, +1]。+1 に近いほどバランス曲線が直線増加。
- **LR Standard Error**:
  ```
  SE = sqrt( Σ (B_i − B̂_i)^2 / N )
  ```
  単位は口座通貨。回帰直線からの平均的乖離幅。

### 1.4 HPR の前提

> **HPR（Holding Period Return）**: 1 トレード単位の収益率 `HPR_i = B_{i+1} / B_i = 1 + p_i / B_i`。MT5 内で「ホールディングピリオドリターン」と訳される。
> - `B_i ≤ 0` は HPR が定義不能のため、MT5 は当該トレードをスキップして N も減算する（証拠金不足直前の挙動）。

---

## 2. ドローダウン系

`Balance` 系と `Equity` 系で**別々に**集計される。差は「未決済含み損益を含むか」。
以下、Balance 系の式のみ示す（Equity 系は `B_k → E_t` に置換すれば同形）。

### 2.1 一覧

| 用語 | 算出式（要点） | `ENUM_STATISTICS` 定数 |
|---|---|---|
| **Balance Min** | `min_k B_k`（中間値） | `STAT_BALANCEMIN` |
| **Balance Drawdown Absolute** | `B_0 − min_k B_k` | (計算値: `STAT_INITIAL_DEPOSIT − STAT_BALANCEMIN`) |
| **Balance Drawdown Maximal**（金額） | `max_k (max_{j≤k} B_j − B_k)` | `STAT_BALANCE_DD` |
| **Balance Drawdown Maximal**（％） | 同じ k での `(peak_k − B_k) / peak_k × 100` | `STAT_BALANCEDD_PERCENT` |
| **Balance Drawdown Relative**（％） | `max_k (peak_k − B_k) / peak_k × 100` | `STAT_BALANCE_DDREL_PERCENT` |
| **Balance Drawdown Relative**（金額） | 上記％を達成した k における `(peak_k − B_k)` | `STAT_BALANCE_DD_RELATIVE` |
| **Equity Min** | `min_t E_t`（中間値） | `STAT_EQUITYMIN` |
| **Equity Drawdown Absolute** | `B_0 − min_t E_t` | (計算値: `STAT_INITIAL_DEPOSIT − STAT_EQUITYMIN`) |
| **Equity Drawdown Maximal**（金額） | 含み損込みの最大下落幅 | `STAT_EQUITY_DD` |
| **Equity Drawdown Maximal**（％） | 同じ点での % | `STAT_EQUITYDD_PERCENT` |
| **Equity Drawdown Relative**（％） | 区間最大下落率 | `STAT_EQUITY_DDREL_PERCENT` |
| **Equity Drawdown Relative**（金額） | 上記％を達成した点での金額 | `STAT_EQUITY_DD_RELATIVE` |

### 2.2 詳細式（Balance 系）

走査用に高値追跡変数 `peak_k = max_{j≤k} B_j` を定義する。

```
peak_k     = max(peak_{k-1}, B_k),   peak_0 = B_0
dd_abs_k   = peak_k − B_k                                ← k 時点の金額 DD
dd_pct_k   = (peak_k − B_k) / peak_k × 100               ← k 時点の％ DD

Balance Drawdown Absolute   = B_0 − min_k B_k
Balance Drawdown Maximal($) = max_k dd_abs_k
Balance Drawdown Maximal(%) = 同じ argmax_k での dd_pct_k
Balance Drawdown Relative(%) = max_k dd_pct_k
Balance Drawdown Relative($) = 同じ argmax_k での dd_abs_k
```

- **Maximal** と **Relative** は最大化軸（金額 vs ％）が異なる。\
  例: ① 初期高値で 1000 →  500 へ下落（金額 500、％ 50）、\
  ② 後半に 10000 → 8500 へ下落（金額 1500、％ 15）の場合:
  - Maximal($) は ②（1500）、Maximal(%) は ② に対応する 15%。
  - Relative(%) は ①（50%）、Relative($) は ① に対応する 500。

### 2.3 Equity 系の特殊性

- Equity は**ティック単位**で更新される。`min_t E_t` は約定間の含み損ピークを捉える。
- 含み損益がスワップ・金利調整で動く分も含む。
- 「Equity DD > Balance DD」が通常。逆転している場合は計測ミスを疑う。

### 2.4 絶対 / 最大 / 相対 の区別（再掲）

> - **Absolute**: 初期残高基準の「最も下まで沈んだ深さ」。直近高値は無視。
> - **Maximal**: 任意の高値からの「最大の下落幅（金額）」。これを最大化する k を選ぶ。
> - **Relative**: 高値からの「最大の下落率（％）」。Maximal とは「最大化する軸」が異なるため一致しない場合がある。

---

## 3. トレード件数・分布系

### 3.1 一覧

| 用語 | 意味 | `ENUM_STATISTICS` 定数 |
|---|---|---|
| **Total Trades** | `N`（確定したトレード往復の総数） | `STAT_TRADES` |
| **Total Deals** | 約定（エントリ+決済+部分決済+SLTP）の総回数 | `STAT_DEALS` |
| **Profit Trades** | `N_w`（勝ちトレード数）／併記 `N_w / N × 100`% | `STAT_PROFIT_TRADES` |
| **Loss Trades** | `N_l`（負けトレード数）／併記 `N_l / N × 100`% | `STAT_LOSS_TRADES` |
| **Long Trades / won %** | `N_long` と `N_long_w / N_long × 100`% | `STAT_LONG_TRADES`, `STAT_PROFIT_LONGTRADES` |
| **Short Trades / won %** | `N_short` と `N_short_w / N_short × 100`% | `STAT_SHORT_TRADES`, `STAT_PROFIT_SHORTTRADES` |
| **Z-Score (Probability)** | 連勝・連敗系列のランダム性検定値 | (計算値・`ENUM_STATISTICS` に該当なし。レポート HTML のみ表示) |

### 3.2 Z-Score 詳細式（Wald-Wolfowitz Runs Test）

連勝・連敗系列を 0/1 の系列とみなし、ラン（連続する同記号の塊）の本数 `R` を数える。

```
N  = 総トレード数
N_w = 勝ち数,  N_l = 負け数 (N_w + N_l = N)
R   = ラン数（勝→負, 負→勝 の符号変化回数 + 1）

期待値: E(R)   = (2·N_w·N_l) / N + 1
分散  : Var(R) = (2·N_w·N_l · (2·N_w·N_l − N)) / (N^2 · (N − 1))
Z     = (R − E(R)) / sqrt(Var(R))
```

- **符号**: 正＝勝敗が「連続しやすい」（順相関）、負＝「交互に出やすい」（逆相関）。
- **有意水準**: `|Z| > 1.96` で 95% 有意。`|Z| > 2.58` で 99% 有意。
- **Probability**: MT5 は併記される確率 `p = 2·(1 − Φ(|Z|))`（両側検定の p 値）。Φ は標準正規分布の累積分布関数。

### 3.3 勝率（Win Rate）

```
WinRate(%)        = N_w / N × 100
LongWinRate(%)    = N_long_w / N_long × 100
ShortWinRate(%)   = N_short_w / N_short × 100
```

- `N = 0` のとき MT5 は 0% を返す。

---

## 4. 個別トレード統計

### 4.1 一覧

| 用語 | 算出式 | `ENUM_STATISTICS` 定数 |
|---|---|---|
| **Largest Profit Trade** | `max_i p_i` | `STAT_MAX_PROFITTRADE` |
| **Largest Loss Trade** | `min_i p_i`（負値） | `STAT_MAX_LOSSTRADE` |
| **Average Profit Trade** | `(Σ_{p_i>0} p_i) / N_w` = `STAT_GROSS_PROFIT / STAT_PROFIT_TRADES` | (計算値) |
| **Average Loss Trade** | `(Σ_{p_i<0} p_i) / N_l` = `STAT_GROSS_LOSS / STAT_LOSS_TRADES` | (計算値) |
| **Maximum Consecutive Wins** (count) | `max_k len(run_w_k)` | `STAT_MAX_CONWINS` |
| **Maximum Consecutive Wins** ($) | 同じラン区間の合計利益 | `STAT_MAX_CONPROFIT_TRADES` |
| **Maximum Consecutive Losses** (count) | `max_k len(run_l_k)` | `STAT_MAX_CONLOSSES` |
| **Maximum Consecutive Losses** ($) | 同じラン区間の合計損失 | `STAT_MAX_CONLOSS_TRADES` |
| **Maximal Consecutive Profit** ($) | `max_k profit(run_w_k)` | `STAT_CONPROFITMAX` |
| **Maximal Consecutive Profit** (count) | 同じラン区間のトレード数 | `STAT_CONPROFITMAX_TRADES` |
| **Maximal Consecutive Loss** ($) | `max_k \|profit(run_l_k)\|` | `STAT_CONLOSSMAX` |
| **Maximal Consecutive Loss** (count) | 同じラン区間のトレード数 | `STAT_CONLOSSMAX_TRADES` |
| **Average Consecutive Wins** | `(Σ_k len(run_w_k)) / K_w` = `N_w / K_w` | `STAT_PROFITTRADES_AVGCON` |
| **Average Consecutive Losses** | `(Σ_k len(run_l_k)) / K_l` = `N_l / K_l` | `STAT_LOSSTRADES_AVGCON` |

ここで `run_w_k` は k 番目の連勝ラン、`K_w` は連勝ラン数。`run_l_k`, `K_l` は連敗側。

### 4.2 連勝・連敗の集計（Maximum vs Maximal）

トレード列を連勝/連敗のラン列 `{run_1, run_2, ...}` に分割する。各ランに対し:

```
len(run_k)    = ラン内のトレード数
profit(run_k) = Σ_{i ∈ run_k} p_i
```

| 指標 | 最大化軸 | 出力（主） | 出力（併記） |
|---|---|---|---|
| **Maximum Consecutive Wins** | `len(run_w_k)` 最大 | `count*` = `max_k len(run_w_k)` | その同じ k の `profit(run_w_k)` |
| **Maximal Consecutive Profit** | `profit(run_w_k)` 最大 | `$*` = `max_k profit(run_w_k)` | その同じ k の `len(run_w_k)` |
| **Maximum Consecutive Losses** | `len(run_l_k)` 最大 | `count*` = `max_k len(run_l_k)` | その同じ k の `\|profit(run_l_k)\|` |
| **Maximal Consecutive Loss** | `\|profit(run_l_k)\|` 最大 | `$*` = `max_k \|profit(run_l_k)\|` | その同じ k の `len(run_l_k)` |

> **Maximum と Maximal の差**:
> - **Maximum** = ラン長（回数）を最大化する区間。
> - **Maximal** = ラン損益（金額）を最大化する区間。
> 連勝が長くても 1 件あたりの利益が小さい場合、2 指標は別の区間を指す。

### 4.3 平均連勝・連敗（Average Consecutive）

```
K_w = 連勝ランの本数
K_l = 連敗ランの本数

AvgConWins   = (Σ_{k=1..K_w} len(run_w_k)) / K_w   = N_w / K_w
AvgConLosses = (Σ_{k=1..K_l} len(run_l_k)) / K_l   = N_l / K_l
```

- `K_w = 0` または `K_l = 0` のとき MT5 は 0 を返す。

---

## 5. 口座・コスト系

### 5.1 残高・証拠金

```
Balance(t)      = B_0 + Σ_{i: close_i ≤ t} p_i           ← 確定損益のみ
FloatingPnL(t)  = Σ_{j ∈ Open(t)} (price_t − entry_j) · lot_j · contract_size · sign_j
Equity(t)       = Balance(t) + FloatingPnL(t) + Swap_open(t) + Commission_open(t)
FreeMargin(t)   = Equity(t) − Margin(t)
MarginLevel(%)  = Equity(t) / Margin(t) × 100        ← Margin(t)=0 のときは ∞ 扱い
```

- `sign_j` = `buy → +1`, `sell → −1`。
- `contract_size`: シンボル仕様（`SYMBOL_TRADE_CONTRACT_SIZE`）。FX 標準ロットなら 100,000。
- **Margin Level** の最低値が `STAT_MIN_MARGINLEVEL`。

### 5.2 1 トレード損益（p_i）の構成

```
p_i = (close_i − entry_i) · sign · lot · contract_size · price_to_account_fx
      + Swap_i
      + Commission_i
```

- `price_to_account_fx`: 決済通貨を口座通貨に換算するレート（クロス時のみ ≠ 1）。
- `Swap_i`: 保有日数 × 翌日繰越金利（買い/売りで別レート）。
- `Commission_i`: ブローカ仕様による。1 ロット定額 / 出来高比例 / ティック比例 のいずれか。

### 5.3 個別項目

| 用語 | 意味・式 |
|---|---|
| **Initial Deposit** | `B_0`（設定タブ） |
| **Balance** | §5.1 |
| **Equity** | §5.1 |
| **Margin** | 必要証拠金。`Σ_j (lot_j · contract_size · entry_j / leverage)`（FX 標準ケース） |
| **Free Margin** | `Equity − Margin` |
| **Margin Level (%)** | `Equity / Margin × 100` |
| **Swap** | `swap_long_rate × open_days`（買い）／`swap_short_rate × open_days`（売り）|
| **Commission** | ブローカ仕様による定数または比例式 |
| **Spread (points)** | `(Ask − Bid) / point_size`。MT5 はティック生成方式に応じ動的／固定で適用 |
| **Slippage** | `\|exec_price − requested_price\| / point_size`。`Deviation` パラメータと連動 |
| **Lot / Volume** | 取引数量。`SYMBOL_VOLUME_STEP` 単位 |

---

## 6. 実行モデル・品質系

| 用語 | 意味 |
|---|---|
| **Ticks modelled** | 生成・使用したティック総数 |
| **Bars in test** | テスト期間のバー本数 |
| **Mismatched chart errors** | 価格生成と保存ヒストリーの不整合検出件数（MT4 互換指標） |
| **Modelling quality (%)** | MT4 由来の品質指標。MT5 では下記「ティック生成方式」に置換される |
| **Every tick** | 1分足から擬似ティックを内挿生成（最も一般的） |
| **Every tick based on real ticks** | ブローカ提供の実ティックを使用（最高精度） |
| **1 minute OHLC** | 1 分足の OHLC 4 点のみで生成（高速・低精度） |
| **Open prices only** | 各バー始値のみで生成（極めて高速） |
| **Math calculations** | 価格を使わない数値計算のみ（カスタム指標最適化用） |

---

## 7. 最適化・カスタム指標系

| 用語 | 意味 |
|---|---|
| **Pass** | 1 パラメータセットでの 1 回のバックテスト実行 |
| **Optimization Criterion** | 最適化評価軸（Balance max / Profit factor max / Expected payoff max / Drawdown min / Recovery factor max / Sharpe Ratio max / Custom max / Complex criterion max） |
| **Custom max** | `OnTester()` 戻り値（double）を最大化する基準 |
| **Complex Criterion** | MT5 内蔵の合成スコア（純益・期待利得・PF・DD・連勝・Sharpe を重み付け平均） |
| **Genetic algorithm** | 進化計算ベースの近似最適化。`generation` / `population` の概念を持つ |
| **Forward / Forward Result** | 最適化期間後ろ 1/2・1/3・1/4 を保持し、独立検証する区間 |

---

## 8. グラフ表示要素

### 8.1 一覧

| 用語 | 算出式 |
|---|---|
| **Balance** 曲線 | `B_k = B_0 + Σ_{i≤k} p_i` を k に対しプロット |
| **Equity** 曲線 | `E_t = Balance(t) + FloatingPnL(t)` をティック時刻 t に対しプロット |
| **LR balance** | §1.3 の `B̂_i = a + b·x_i` の直線 |
| **MFE (Maximum Favorable Excursion)** | §8.2 |
| **MAE (Maximum Adverse Excursion)** | §8.2 |
| **Holding time** | `close_time_i − open_time_i`（秒・分・時間・日で集計） |

### 8.2 MFE / MAE 詳細式

i 番目トレードの保有期間 `[t_open_i, t_close_i]` における tick 価格列を `{price_t}` とし、エントリ価格 `entry_i`, 方向 `sign_i ∈ {+1, −1}` とする:

```
UnrealizedPnL_i(t) = (price_t − entry_i) · sign_i · lot_i · contract_size

MFE_i = max_{t ∈ [t_open_i, t_close_i]} UnrealizedPnL_i(t)        ← ≥ 0 とは限らない
MAE_i = min_{t ∈ [t_open_i, t_close_i]} UnrealizedPnL_i(t)        ← ≤ 0 とは限らない
```

- 「Excursion」は方向付きの最大移動。MFE は含み益のピーク、MAE は含み損のボトム。
- 単位は金額（口座通貨）または points（`UnrealizedPnL` を `point_size` で割れば points）。
- MT5 は MFE/MAE をトレード単位で記録し、グラフではトレード番号に対する散布図として表示する。

> MFE/MAE は「決済タイミングの最適化余地」を示す。
> - `mean(MFE) ≫ mean(p_w)` なら TP を引き上げる余地あり。
> - `mean(\|MAE\|) ≫ mean(\|p_l\|)` なら早期撤退の余地あり。
> - `MFE_i − p_i` は「取り逃した含み益」、`p_i − MAE_i` は「耐え抜いた含み損からの戻し」。

---

## 9. バックテストタブ（明細）の列

| 列名 | 意味 |
|---|---|
| **Time** | サーバ時刻 |
| **Deal / Order** | 約定／注文 ID |
| **Symbol** | 銘柄 |
| **Type** | buy / sell / buy limit / sell stop / s/l / t/p / out / out by |
| **Direction** | in（建て）/ out（決済）/ in/out（両建て両決済） |
| **Volume** | 約定数量 |
| **Price** | 約定価格 |
| **Order** | 関連注文 ID |
| **Commission / Swap / Profit** | 当該約定の手数料・スワップ・確定損益 |
| **Balance** | 約定直後のバランス |
| **Comment** | EA からの注文コメント（`MqlTradeRequest.comment`） |

---

## 10. MQL5 内からの参照方法

`OnTester()` 内で `TesterStatistics(ENUM_STATISTICS id)` を呼ぶと、上表の各値を double で取得できる。主な定数:

```mq5
// 損益サマリー
double init  = TesterStatistics(STAT_INITIAL_DEPOSIT);
double net   = TesterStatistics(STAT_PROFIT);
double gp    = TesterStatistics(STAT_GROSS_PROFIT);
double gl    = TesterStatistics(STAT_GROSS_LOSS);
double pf    = TesterStatistics(STAT_PROFIT_FACTOR);
double rf    = TesterStatistics(STAT_RECOVERY_FACTOR);
double ep    = TesterStatistics(STAT_EXPECTED_PAYOFF);
double sharp = TesterStatistics(STAT_SHARPE_RATIO);
double mml   = TesterStatistics(STAT_MIN_MARGINLEVEL);

// ドローダウン（Balance / Equity 系）
double bal_min = TesterStatistics(STAT_BALANCEMIN);
double bal_dd  = TesterStatistics(STAT_BALANCE_DD);
double bal_dd_pct = TesterStatistics(STAT_BALANCEDD_PERCENT);
double bal_ddrel_pct = TesterStatistics(STAT_BALANCE_DDREL_PERCENT);
double bal_ddrel = TesterStatistics(STAT_BALANCE_DD_RELATIVE);
double eq_min  = TesterStatistics(STAT_EQUITYMIN);
double eq_dd   = TesterStatistics(STAT_EQUITY_DD);
double eq_dd_pct = TesterStatistics(STAT_EQUITYDD_PERCENT);
double eq_ddrel_pct = TesterStatistics(STAT_EQUITY_DDREL_PERCENT);
double eq_ddrel = TesterStatistics(STAT_EQUITY_DD_RELATIVE);

// 件数・分布
int    deals = (int)TesterStatistics(STAT_DEALS);
int    trades= (int)TesterStatistics(STAT_TRADES);
int    pt    = (int)TesterStatistics(STAT_PROFIT_TRADES);
int    lt    = (int)TesterStatistics(STAT_LOSS_TRADES);
int    lng   = (int)TesterStatistics(STAT_LONG_TRADES);
int    sht   = (int)TesterStatistics(STAT_SHORT_TRADES);
int    plng  = (int)TesterStatistics(STAT_PROFIT_LONGTRADES);
int    psht  = (int)TesterStatistics(STAT_PROFIT_SHORTTRADES);

// 個別トレード統計
double max_p = TesterStatistics(STAT_MAX_PROFITTRADE);
double max_l = TesterStatistics(STAT_MAX_LOSSTRADE);
int    mcw   = (int)TesterStatistics(STAT_MAX_CONWINS);     // 最長連勝（回数）
double mcw_$ = TesterStatistics(STAT_MAX_CONPROFIT_TRADES); // その区間の利益
int    mcl   = (int)TesterStatistics(STAT_MAX_CONLOSSES);   // 最長連敗（回数）
double mcl_$ = TesterStatistics(STAT_MAX_CONLOSS_TRADES);   // その区間の損失
double cpmax = TesterStatistics(STAT_CONPROFITMAX);         // 最大連続利益（金額）
int    cpmax_n = (int)TesterStatistics(STAT_CONPROFITMAX_TRADES);
double clmax = TesterStatistics(STAT_CONLOSSMAX);           // 最大連続損失（金額）
int    clmax_n = (int)TesterStatistics(STAT_CONLOSSMAX_TRADES);
double avg_cw = TesterStatistics(STAT_PROFITTRADES_AVGCON);
double avg_cl = TesterStatistics(STAT_LOSSTRADES_AVGCON);

// カスタム最適化基準
double custom = TesterStatistics(STAT_CUSTOM_ONTESTER);
```

> `OnTester()` の戻り値（double）が **Custom max** の最適化基準となる。複数指標を重み付け合成する場合はここで計算する。返却値は `STAT_CUSTOM_ONTESTER` で再取得できる。

---

## 11. Python 再現時の対応表（要点）

`df` は確定トレード DataFrame（`profit`, `is_long`, `open_time`, `close_time`, `balance` 列を含む）、`equity` は ティック単位の `pd.Series` とする。

| MT5 用語 | Python 実装 |
|---|---|
| Total Net Profit | `df.profit.sum()` |
| Gross Profit | `df.loc[df.profit > 0, 'profit'].sum()` |
| Gross Loss | `df.loc[df.profit < 0, 'profit'].sum()` |
| Profit Factor | `df.loc[df.profit>0,'profit'].sum() / abs(df.loc[df.profit<0,'profit'].sum())` |
| Expected Payoff | `df.profit.mean()` |
| Recovery Factor | `df.profit.sum() / equity_dd_max_abs` |
| Balance Drawdown Maximal ($) | `(df.balance.cummax() - df.balance).max()` |
| Balance Drawdown Maximal (%) | `(((df.balance.cummax() - df.balance) / df.balance.cummax()) * 100).iloc[arg]` （arg = $ DD の argmax） |
| Balance Drawdown Relative (%) | `(((df.balance.cummax() - df.balance) / df.balance.cummax()) * 100).max()` |
| Equity Drawdown Maximal ($) | `(equity.cummax() - equity).max()` |
| Equity Drawdown Maximal (%) | `(((equity.cummax() - equity) / equity.cummax()) * 100).iloc[arg]` |
| HPR | `hpr = df.balance / df.balance.shift(1); hpr.iloc[0] = (df.balance.iloc[0]) / B0` |
| AHPR | `hpr.mean()` |
| GHPR | `hpr.prod() ** (1 / len(hpr))` |
| Sharpe Ratio | `(hpr.mean() - 1) / hpr.std(ddof=0)` ※ 母分散（ddof=0） |
| LR Correlation | `np.corrcoef(np.arange(len(balance)), balance)[0, 1]` |
| LR Standard Error | `np.sqrt(((balance - poly1d_fit(x))**2).mean())` |
| Largest Profit / Loss | `df.profit.max()` / `df.profit.min()` |
| Average Profit Trade | `df.loc[df.profit>0, 'profit'].mean()` |
| Average Loss Trade | `df.loc[df.profit<0, 'profit'].mean()` |
| Maximum Consecutive Wins (count) | `max_run_length(df.profit > 0)` |
| Maximal Consecutive Profit ($) | `max_run_sum(df.profit, lambda p: p > 0)` |
| Z-Score | Wald-Wolfowitz Runs Test（§3.2） |
| MFE / MAE | tick データに対し `(price - entry) * sign` の max / min を保有区間で計算 |

ヘルパー関数の参考実装:

```python
def max_run_length(mask: pd.Series) -> int:
    """連続 True の最大長。"""
    if not mask.any():
        return 0
    groups = (mask != mask.shift()).cumsum()
    return mask.groupby(groups).sum().max()

def max_run_sum(values: pd.Series, predicate) -> float:
    """predicate を満たすランの合計値の最大。"""
    mask = predicate(values)
    if not mask.any():
        return 0.0
    groups = (mask != mask.shift()).cumsum()
    sums = values.where(mask).groupby(groups).sum().dropna()
    return sums.max() if len(sums) else 0.0

def zscore_runs(wins: pd.Series) -> float:
    """Wald-Wolfowitz Runs Test の Z 値。wins は bool 列。"""
    N   = len(wins)
    Nw  = int(wins.sum())
    Nl  = N - Nw
    R   = int((wins != wins.shift()).sum())  # ラン数
    if Nw == 0 or Nl == 0 or N < 2:
        return 0.0
    ER  = 2 * Nw * Nl / N + 1
    VarR = 2 * Nw * Nl * (2 * Nw * Nl - N) / (N**2 * (N - 1))
    return (R - ER) / VarR**0.5
```

---

## 12. 数値例による照合（10 トレード）

§1〜§4 の式を、簡単な 10 トレード系列で実際に計算して照合する。Python 実装側でも同じ値が出れば一致確認となる。

### 12.1 入力

```
B_0 = 10000
p   = [+150, -80, +220, +60, -300, -50, +400, -120, +90, -40]
方向 (sign) = [L, S, L, L, S, S, L, S, L, S]   ← L=Long, S=Short
```

`p_i` 適用後の Balance 系列:

| i | p_i | B_i | peak_i | DD_i ($) | DD_i (%) |
|---|---|---|---|---|---|
| 0 | (init) | 10000 | 10000 | 0 | 0.00 |
| 1 | +150 | 10150 | 10150 | 0 | 0.00 |
| 2 | −80 | 10070 | 10150 | 80 | 0.79 |
| 3 | +220 | 10290 | 10290 | 0 | 0.00 |
| 4 | +60 | 10350 | 10350 | 0 | 0.00 |
| 5 | −300 | 10050 | 10350 | 300 | 2.90 |
| 6 | −50 | 10000 | 10350 | 350 | 3.38 |
| 7 | +400 | 10400 | 10400 | 0 | 0.00 |
| 8 | −120 | 10280 | 10400 | 120 | 1.15 |
| 9 | +90 | 10370 | 10400 | 30 | 0.29 |
| 10 | −40 | 10330 | 10400 | 70 | 0.67 |

### 12.2 損益サマリー（§1）

```
N = 10
GrossProfit = 150 + 220 + 60 + 400 + 90 = 920
GrossLoss   = -80 - 300 - 50 - 120 - 40 = -590
Net Profit  = 920 - 590 = 330
Profit Factor    = 920 / 590         = 1.5593
Expected Payoff  = 330 / 10          = 33.00
```

HPR 計算（各 `HPR_i = B_i / B_{i-1}`）:

```
HPR = [1.01500, 0.99212, 1.02185, 1.00583, 0.97101,
       0.99502, 1.04000, 0.98846, 1.00876, 0.99614]
AHPR = mean(HPR) = 1.003419
GHPR = prod(HPR)^(1/10) = (10330/10000)^(1/10) = 1.003250
σ(HPR) (ddof=0) = 0.020019
Sharpe = (1.003419 - 1) / 0.020019 = 0.1708
```

Recovery Factor は §12.3 の Balance DD と合わせて:

```
Balance DD Max ($) = 350         (§12.3 参照)
Recovery Factor    = |330| / 350 = 0.9429
```

### 12.3 ドローダウン（§2）

```
Balance Min                  = min(B_k) = 10000   (i=6)
Balance DD Absolute          = 10000 - 10000 = 0
Balance DD Maximal ($)       = max(DD_i $) = 350  (i=6)
Balance DD Maximal (%)       = 350 / 10350 × 100 = 3.3816
Balance DD Relative (%)      = max(DD_i %) = 3.3816 (i=6, 同じ)
Balance DD Relative ($)      = 350
```

> この系列では Maximal と Relative の最大化点が一致した（同じ i=6）。一致しないケースは §2.2 の注意例を参照。

### 12.4 トレード件数・分布（§3）

```
N_w = 5, N_l = 5
WinRate = 50.0 %

Long  trades: i = 1, 3, 4, 7, 9   → 5 件、勝ち = i=1,3,4,7,9 のうち +150,+220,+60,+400,+90 = 5 件勝ち → 100.0 %
Short trades: i = 2, 5, 6, 8, 10  → 5 件、勝ち = なし → 0.0 %

連勝・連敗ラン: W L WW LL W L W L
  → 連勝ラン: [{1}, {3,4}, {7}, {9}]            K_w = 4, 長さ = [1,2,1,1]
  → 連敗ラン: [{2}, {5,6}, {8}, {10}]           K_l = 4, 長さ = [1,2,1,1]

Z-Score 計算:
  R = ラン数 = 8 (=K_w + K_l)
  E(R)   = 2·5·5 / 10 + 1 = 6.0
  Var(R) = 2·5·5 · (50 - 10) / (100 · 9) = 50·40/900 = 2.2222
  Z      = (8 - 6) / sqrt(2.2222) = 2 / 1.4907 = 1.3416
```

|Z| < 1.96 なので 95% 有意ではない（ランダム配列と区別できない）。

### 12.5 個別トレード統計（§4）

```
Largest Profit Trade = +400        (i=7)
Largest Loss Trade   = -300        (i=5)
Average Profit Trade = 920 / 5     = 184.00
Average Loss Trade   = -590 / 5    = -118.00

連勝ラン分析:
  長さ:  [1, 2, 1, 1]
  利益:  [+150, +220+60=+280, +400, +90]

Maximum Consecutive Wins (count) = max([1,2,1,1]) = 2          (run = {3,4})
Maximum Consecutive Wins ($)     = その区間の利益 = +280
Maximal Consecutive Profit ($)   = max([150,280,400,90]) = +400 (run = {7})
Maximal Consecutive Profit (cnt) = その区間のトレード数 = 1
Average Consecutive Wins         = 5 / 4 = 1.25

連敗ラン分析:
  長さ:  [1, 2, 1, 1]
  損失:  [-80, -300-50=-350, -120, -40]

Maximum Consecutive Losses (count) = max([1,2,1,1]) = 2         (run = {5,6})
Maximum Consecutive Losses ($)     = その区間の損失 = -350
Maximal Consecutive Loss ($)       = max(|loss|) = 350          (run = {5,6}, 同じ)
Maximal Consecutive Loss (cnt)     = その区間のトレード数 = 2
Average Consecutive Losses         = 5 / 4 = 1.25
```

> この系列では「最長連敗」と「最大連続損失額」が偶然同じ区間（{5,6}）になった。本来は §4.2 のとおり別区間になり得る。

### 12.6 期待される MT5 出力との対応

```
STAT_INITIAL_DEPOSIT          = 10000.00
STAT_PROFIT                   = 330.00
STAT_GROSS_PROFIT             = 920.00
STAT_GROSS_LOSS               = -590.00
STAT_PROFIT_FACTOR            = 1.56
STAT_EXPECTED_PAYOFF          = 33.00
STAT_RECOVERY_FACTOR          = 0.94
STAT_SHARPE_RATIO             = 0.17
STAT_BALANCEMIN               = 10000.00
STAT_BALANCE_DD               = 350.00
STAT_BALANCEDD_PERCENT        = 3.38
STAT_BALANCE_DDREL_PERCENT    = 3.38
STAT_BALANCE_DD_RELATIVE      = 350.00
STAT_TRADES                   = 10
STAT_PROFIT_TRADES            = 5
STAT_LOSS_TRADES              = 5
STAT_LONG_TRADES              = 5
STAT_SHORT_TRADES             = 5
STAT_PROFIT_LONGTRADES        = 5
STAT_PROFIT_SHORTTRADES       = 0
STAT_MAX_PROFITTRADE          = 400.00
STAT_MAX_LOSSTRADE            = -300.00
STAT_MAX_CONWINS              = 2
STAT_MAX_CONPROFIT_TRADES     = 280.00
STAT_MAX_CONLOSSES            = 2
STAT_MAX_CONLOSS_TRADES       = -350.00
STAT_CONPROFITMAX             = 400.00
STAT_CONPROFITMAX_TRADES      = 1
STAT_CONLOSSMAX               = -350.00
STAT_CONLOSSMAX_TRADES        = 2
STAT_PROFITTRADES_AVGCON      = 1.25
STAT_LOSSTRADES_AVGCON        = 1.25
```

Python 実装側で `assert` テストする際の照合値として利用する。

---

## 付録: 用語が出現する画面と参照頻度

| カテゴリ | 設定 | 結果 | グラフ | バックテスト | 最適化 |
|---|:-:|:-:|:-:|:-:|:-:|
| 損益サマリー | – | ◎ | – | – | ◎ |
| ドローダウン | – | ◎ | ○ | – | ◎ |
| 件数・分布 | – | ◎ | – | – | ○ |
| 個別トレード統計 | – | ◎ | – | ○ | – |
| 口座・コスト | ◎ | ○ | – | ◎ | – |
| 実行モデル品質 | ◎ | ○ | – | – | – |
| 最適化指標 | – | – | – | – | ◎ |
| グラフ要素 | – | – | ◎ | – | ○ |

◎: 主要表示／○: 補助表示／–: 表示なし
