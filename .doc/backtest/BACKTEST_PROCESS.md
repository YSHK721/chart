# バックテスト処理ステップ仕様書（Python 実装向け）

`sample/MQL5/` のカスタム EA・指標を精査し、**バックテストエンジンが実行すべき処理ステップ**を
時系列の手順として決定論的に記述する。本書は「何を売買するか（戦略ロジック）」ではなく
**「バックテストをどう回すか（実行フロー）」** を対象とする。

- **戦略ロジック（条件式・SL/TP・発注方式）**: [`./BACKTEST_SPEC.md`](./BACKTEST_SPEC.md) を参照（重複記載しない）。
- **分析結果の用語・算出式**: [`./BACKTEST_METRICS.md`](./BACKTEST_METRICS.md) を参照。
- **Python 設計仕様（9 項目）**: [`./BACKTEST_DESIGN.md`](./BACKTEST_DESIGN.md) を参照。
- **移植上の配列向き・型注意**: [`./PORTING_GUIDE.md`](./PORTING_GUIDE.md) を参照。
- **出典**: `sample/MQL5/Experts/`（カスタム EA 7本）、`sample/MQL5/Indicators/MADiff.mq5`。
- **記述方針**: コードに書かれた事実のみ。⚠️ は再現精度に直結する非決定要因・原典バグ。

---

## 0. MT5 バックテスト実行モデル（前提）

Python で再現する前に、MT5 ストラテジテスターが EA を駆動する仕組みを押さえる。
これがそのまま「メインループの骨格」になる。

### 0.1 イベントライフサイクル

EA は関数ではなくイベントハンドラの集合で、テスターが以下の順で呼ぶ。

| イベント | 呼出タイミング | 本プロジェクトでの用途 |
|---|---|---|
| `OnInit()` | テスト開始時に1回 | 指標ハンドル生成・`_Digits` 補正（#5）・状態変数初期化 |
| `OnTick()` | **各ティック毎** | シグナル評価・発注・決済（全 EA の主処理） |
| `OnTimer()` | `EventSetTimer(秒)` 設定時、周期的 | #4 のみ：保有ポジの SL/TP 更新 |
| `OnDeinit()` | テスト終了時に1回 | ハンドル解放（バックテスト結果に影響なし） |

→ **Python メインループ＝「OnInit 相当の前処理」→「価格イベント列を1件ずつ OnTick 相当へ流す」**。

### 0.2 ティックモデリング（結果を左右する最重要設定）

MT5 テスターには3つの足解像度モードがある。どれを選ぶかで約定・SL/TP ヒットが変わる。

| モード | 1足あたりの OnTick 回数 | 価格列 |
|---|---|---|
| 始値のみ（Open prices only） | 1回（足始値で） | open のみ |
| 1分足 OHLC（1 Min OHLC） | 4回（O→H→L→C の順） | OHLC を4点に展開 |
| 全ティック（Every tick） | 実ティック数 | 実 Bid/Ask 列が必要 |

⚠️ **#1〜#4 は毎ティック評価**のため「全ティック」モードを前提に設計されている。
「始値のみ」で回すと同一足内の複数シグナル・SL/TP ヒット順序が再現できない。
**#5 は新規バー1回**処理なので解像度の影響を受けにくい（§2 と同じ結論）。

→ Python では「全ティック」を第一目標とし、ティックデータが無い場合は
**1分足 OHLC を O→H→L→C の4疑似ティックに展開**して近似する（§1.3）。

### 0.3 シリーズ配列の向き

MQL の `ArraySetAsSeries(true)` 配列は `[0]`＝最新足、`[1]`＝1本前。Python では昇順
（古い→新しい）に持ち、`[0]→df.iloc[-1]`, `[1]→df.iloc[-2]` と読み替える（PORTING_GUIDE §4-3）。

---

## 1. データパイプライン（OnInit 前の準備）

### 1.1 入力データ要件

| データ | 用途 | 必須 EA |
|---|---|---|
| OHLC（足の open/high/low/close）＋足時刻 | 指標計算・新規バー判定・SL/TP 監視 | 全 EA |
| Bid/Ask（またはスプレッド） | 約定価格・SL/TP ヒット判定 | 全 EA（買=Ask, 売=Bid） |
| ティック列（時刻＋Bid/Ask） | 毎ティック評価の忠実再現 | #1〜#4（無ければ OHLC 展開で近似） |
| 取引セッションカレンダー | `MarketIsOpen()` 判定 | #2 のみ |

⚠️ **スプレッドは必須**。全 EA が買い＝Ask・売り＝Bid で発注し、SL/TP も Ask/Bid 基準で
ヒット判定する。`Ask = Bid + spread` のモデルが無いと約定とヒットがズレる。

### 1.2 指標の事前計算（precompute）

MT5 は `iCustom`/`iMA`/`iADX` ハンドルを通じ、**各足について確定済みの指標値**を
`CopyBuffer` で取り出す。バックテストでは毎ティック再計算せず、**足単位で1回だけ全系列を
前計算**し、ティック処理時はその足のインデックスを引くだけにする（MT5 の `OnCalculate` も
`prev_calculated` で差分更新する増分計算＝同じ思想）。

必要な指標と算出元（移植済み部品で構成可能か）:

| 指標 | 定義 | 参照 EA | 移植状況 |
|---|---|---|---|
| **MADiff** | `MA(close,P,M) − MA(open,P,M)`（同一足） | #1〜#4 | `moving_averages` ＋ `common.applied_price` で構成可 |
| **EMA(8, close)** | 指数移動平均 | #5 | `moving_averages.exponential_ma` |
| **ADX(8)/+DI/−DI** | Wilder の ADX 3バッファ | #5 | ⚠️ 未移植（要実装） |
| **Band**（28バッファ四分位） | pOL/pOH 等 | #4 | ⚠️ ソース不在（`indicators/profit_band` で代替要照合） |

**MADiff の前計算ステップ**（`Indicators/MADiff.mq5` の `OnCalculate` 準拠）:
1. `MA_open[i] = MA(open[0..i], MAPeriod, MAMethod)` を全足分。
2. `MA_close[i] = MA(close[0..i], MAPeriod, MAMethod)` を全足分。
3. `MADiff[i] = MA_close[i] − MA_open[i]`。
4. `i < MAPeriod − 1` の足は未確定（描画開始前）。NaN 扱いにし、EA 側で参照しない。

⚠️ **MAPeriod/MAMethod は EA が `iCustom` で渡す値**が指標既定（14/SMA）に優先する
（#1: 5/EMA, #2: 14/SMA, #3: 5/EMA, #4: 無指定＝14/SMA）。EA ごとに別系列を前計算する。

### 1.3 ティック列の生成（全ティックデータが無い場合）

1分足 OHLC を1足あたり4疑似ティックへ展開する近似:
1. 始値ティック: `price = open`、足時刻。
2. 高値ティック: `price = high`。
3. 安値ティック: `price = low`。
4. 終値ティック: `price = close`。
- 各疑似ティックに `Ask = price + spread/2`, `Bid = price − spread/2`（または `Ask=price+spread, Bid=price` 等、採用モデルを固定）。
- ⚠️ 同一足内の高値・安値どちらが先かは未知。MT5 既定は「始値が高安どちらに近いかで O→H→L→C か O→L→H→C を切替」。**この順序が SL/TP ヒット判定に影響**するため、採用ルールを明記し固定する。

---

## 2. メインループの処理ステップ（共通骨格）

全 EA に共通する1イベント（OnTick）あたりの処理順序。EA 固有の差分は §3。

```
[テスト開始]
  OnInit:
    1. 指標ハンドル相当の前計算系列を用意（§1.2）
    2. 状態変数を初期化（prevMADiff=0, pendingBuyOrder=false, tradeCount=0,
       lastTradeDay=0, Old_Time=0 など EA 固有）
    3. #5 のみ: _Digits により STP/TKP を×10 するか確定（§BACKTEST_SPEC 3.5）

[ティックイベント列を1件ずつ処理] for each tick t:
  OnTick(t):
    A. ガード判定（EA 固有・早期 return）
       - #5: Bars < 60 なら return
       - #2: 取引許可・セッション判定
    B. 新規バー判定（#5 のみ）
       - 現足時刻 New_Time[0] が前回保存 Old_Time と異なる初回ティックのみ IsNewBar=true
       - IsNewBar==false なら return（同一足の2回目以降は処理しない）
    C. 指標値の取得（前計算系列から現足インデックスを引く）
       - curr = 指標[現足], prev = 指標[1本前] または static 保持の前ティック値
    D. 保有ポジション状態の取得
       - 同方向ポジの有無（IsTradeOpen / PositionSelect）
    E. シグナル評価（§BACKTEST_SPEC の条件式）
    F. 発注 / 決済アクション（§4・§5）
    G. 状態変数の更新（prevMADiff = currMADiff 等）
  [F の発注後] → ポジションを「未決済ポジション集合」へ追加

  各ティック末尾（または各足）:
    H. 保有ポジションの SL/TP ヒット判定（§5）→ ヒットしたら決済・損益確定
    I. エクイティ/残高の更新（§6）

[テスト終了]
  OnDeinit: 統計を集計し出力（§6）
```

⚠️ **F（新規発注）と H（SL/TP 監視）の順序**: 実機は別ティックで起きるが、バックテストでは
同一ティック内で「発注 → 同ティックで即 SL/TP 監視」とすると非現実的な即時決済が起こり得る。
**発注した足の次ティック以降から SL/TP 監視を開始**するのが安全（採用ルールを固定）。

---

## 3. EA 別の処理ステップ（OnTick の実行手順）

各 EA の `OnTick` を、Python の1イベント処理として手順化する。条件式の詳細は BACKTEST_SPEC §3。

### 3.1 #1 TC24051901 — 毎ティック・ゼロクロス両建て

```
OnTick(t):
  1. MADiff 系列から curr=MADiff[現足], prev=MADiff[1本前] を取得
  2. 買い: prev<0 && curr>0 かつ 買いポジ無し → 成行買い（price=Ask, SL/TP固定）
  3. 売り: prev>0 && curr<0 かつ 売りポジ無し → 成行売り（price=Bid, SL/TP固定）
  （買い else if 売り：同一ティックで両方は出ない）
```
- **状態変数なし**（prev は配列 [1] から毎回取得）。最も単純。再現の基準にする。
- ⚠️ `IsTradeOpen` は同方向のみ禁止。反対方向ポジは同時保有可（両建て）。

### 3.2 #2 TC24051902 — 毎ティック評価・日付変更で執行（セッション依存）

```
OnTick(t):
  1. 取引許可チェック → 不可なら return
  2. curr = MADiff[現足]（CopyBuffer count=1）。prev は static 変数（前ティック値）
  3. currentTime = ティック時刻。day = currentTime / 86400（整数日）
  4. 【日付変更ブロック】 lastTradeDay != day のとき:
       a. tradeCount = 0; lastTradeDay = day
       b. pendingBuyOrder && MarketIsOpen() → 成行買い執行（前日予約を翌日寄付で）→ pending=false
       c. curr < 0 → 保有買いポジを全クローズ（ClosePositions BUY）
  5. 【シグナル検出ブロック】 tradeCount<MaxTradesPerDay && (!CheckMarketHours||MarketIsOpen()):
       a. curr>0 && prev<=0 → pendingBuyOrder = true（当日予約のみ・発注はしない）
  6. prev = curr（state 更新）
```
- ⚠️ **予約（5a）と執行（4b）は別日**。予約した翌日の最初の日付変更ティックで初めて発注。
- ⚠️ `tradeCount` をインクリメントするコードが無く `MaxTradesPerDay` 制限は実質無効。原典踏襲。
- `MarketIsOpen()`: 週末除外＋`SymbolInfoSessionTrade`（曜日・セッション0〜2の時刻範囲）。
  → Python では**ブローカーのセッションカレンダーを入力に持つ**必要がある（§1.1）。

### 3.3 #3 TC24051903 — 毎ティック・符号・反転全決済

```
OnTick(t):
  1. curr = MADiff[現足]（count=1）
  2. curr>0 のとき:
       - 保有売りがあれば PositionClose（反転決済）
       - PositionsTotal()==0 なら trade.Buy(1)  ← ⚠️数量リテラル1ロット
  3. curr<0 のとき:
       - 保有買いがあれば PositionClose（反転決済）
       - 売り発注は原典でコメントアウト（実行されない）
```
- 固定 SL/TP **なし**。決済は符号反転時の PositionClose のみ。
- ⚠️ 数量は `LotSize` でなくリテラル `1`。実質「買い専＋反転決済」。

### 3.4 #5 PRO!fit_Band / my_first_ea — 新規バー1回・ADX+EMA+DI

```
OnTick(t):
  1. Bars<60 → return
  2. 新規バー判定: CopyTime で現足時刻取得 → Old_Time と異なる初回のみ IsNewBar=true
     IsNewBar==false → return  ← 同一足の2回目以降は完全スキップ
  3. （新規バー確定後）最新 Bid/Ask 取得、直近3足 OHLC・ADX(3点)・+DI・−DI・EMA(3点)取得
  4. 保有判定: PositionSelect → Buy_opened / Sell_opened
  5. p_close = mrate[1].close（1本前の確定終値）
  6. 買い条件 AND: EMA[0]>EMA[1]>EMA[2] && p_close>EMA[1] && ADX[0]>22 && +DI[0]>−DI[0]
       → 買いポジ無ければ 成行買い（price=Ask, SL=Ask−STP*_Point, TP=Ask+TKP*_Point）
  7. 売り条件 AND: EMA[0]<EMA[1]<EMA[2] && p_close<EMA[1] && ADX[0]>22 && +DI[0]<−DI[0]
       → 売りポジ無ければ 成行売り（price=Bid, SL=Bid+STP*_Point, TP=Bid−TKP*_Point）
```
- ⚠️ **新規バーの最初のティック**で評価。指標 `[0]` は形成直後の現足（ほぼ前足確定値だが厳密には現足）。
- ⚠️ STP/TKP は `_Digits∈{3,5}` で×10（OnInit で確定済み）。テスト銘柄の桁数を固定すること。
- ⚠️ magic でポジを絞らない＝同シンボルの他ポジを誤検知し得る（単独運用前提なら軽微）。

### 3.5 #4 TC24051903_24052301 — タイマー＋ティック・BuyLimit 指値

```
OnInit:  EventSetTimer(PeriodSeconds()-60)
OnTick(t):
  1. 時間枠経過（ShouldUpdate）なら Band(28バッファ) と MADiff[0] を更新
  2. 発注条件（毎ティック評価）:
       MADiff[0] < -1 && (pOL51 + SYMBOL_SPREAD < Bid) && PositionsTotal()==0 && OrdersTotal()==0
       → trade.BuyLimit(volume, 指値=pOL98, SL=pOL99, TP=pOH99,
                        ORDER_TIME_SPECIFIED, 期限=iTime(0)+PeriodSeconds()-60)
OnTimer:  保有ポジの SL を建値へ、TP を pOH99 へ更新
```
- ⚠️ **BuyLimit＝指値の保留注文**。発注≠約定。価格が指値(pOL98)に**到達したティックで約定**する
  処理（§4.2）が別途必要。期限到達まで未約定なら失効。
- ⚠️ SL/TP は Band の**絶対価格値**を直接使用（_Point 換算なし）。
- ⚠️ `volume = (long)AccountInfoDouble(ACCOUNT_MARGIN_FREE)`（証拠金余剰額をロット数に直代入＝巨大ロット）。
- ⚠️ `SYMBOL_SPREAD` を関数でなく**列挙定数のまま加算**（実スプレッドでない原典バグ）。
- ⚠️ `Band` 指標ソース不在のため #4 は完全再現不可（BACKTEST_SPEC §4）。

### 3.6 #6 range — スケルトン（処理なし）

`OnTick` が空。売買ロジック未実装＋依存指標不在。**バックテスト対象外**。

---

## 4. 発注処理ステップ（注文の種類別）

### 4.1 成行注文（TRADE_ACTION_DEAL）— #1/#2/#3/#5

```
1. 約定価格を確定: 買い=現ティックの Ask, 売り=現ティックの Bid
2. SL/TP を価格で確定（固定 points×_Point、または指標値直接）
3. ポジションを生成し「未決済ポジション集合」へ追加:
   { side, entry_price, volume, sl, tp, entry_time, magic }
4. （バックテストでは即時約定とみなす。スリッページ deviation は無視可）
```
- スプレッドは entry_price に内包（買い=Ask で買い、決済時は Bid で評価＝往復スプレッドコスト）。

### 4.2 指値注文（BuyLimit, ORDER_TIME_SPECIFIED）— #4 のみ

```
1. 保留注文を「保留注文集合」へ追加: { limit_price=pOL98, sl, tp, expire_time }
2. 以後の各ティックで:
   a. Ask <= limit_price に到達 → 約定（ポジション化し未決済集合へ移動）
   b. tick_time >= expire_time かつ未約定 → 注文失効（削除）
```

### 4.3 決済発注 — #2 ClosePositions / #3 PositionClose

```
- 反対サイド価格で決済: 買いの決済=Bid, 売りの決済=Ask
- 損益 = (決済価格 − entry_price) × volume × 契約サイズ（買い）／符号反転（売り）
```

---

## 5. ポジション管理と SL/TP ヒット判定

固定 SL/TP を持つ #1・#5（および #4 のタイマー更新後）は、各ティックで保有ポジを走査し
**足の高安が SL/TP を貫いたか**を判定する。

```
for pos in 未決済ポジション集合:
  買いポジ:
    - low  <= pos.sl → SL ヒット → 決済価格 = pos.sl（保守側）
    - high >= pos.tp → TP ヒット → 決済価格 = pos.tp
  売りポジ:
    - high >= pos.sl → SL ヒット
    - low  <= pos.tp → TP ヒット
  → ヒットしたら損益確定・ポジ削除
```

⚠️ **同一足で SL と TP の両方に到達**した場合（高安が両方を包含）、どちらが先かは
足データだけでは決まらない。MT5 既定は「より不利な側（SL）を優先」する保守モデル。
**SL 優先を採用ルールとして固定**し、全ティックモードでは実ティック順で判定する。

⚠️ **SL/TP 価格は Bid/Ask どちら基準か**: MT5 は買いポジの SL/TP を Bid で、売りポジを Ask で
評価する（決済は反対サイド）。ヒット判定にも同じサイドの価格列を使うこと。

---

## 6. 損益・統計の集計（OnDeinit 相当）

各決済イベントで損益を確定し累積する。最低限の集計項目:

```
- 残高(balance): 確定損益の累積
- エクイティ(equity): balance + 未決済ポジの含み損益（各ティックで再評価）
- トレード記録: entry/exit 時刻・価格・side・volume・損益・決済理由(SL/TP/反転/失効)
- 集計指標: 総損益, 総利益, 総損失, 勝率, PF, 損益レシオ, 期待利得, 最大DD, リカバリーF, シャープ(年率), 最大連勝/連敗, 最大利益/損失トレード, トレード数（各定義は §6.1）
```
- 含み損益も Ask/Bid 基準（買い＝現 Bid で評価、売り＝現 Ask で評価）。
- ⚠️ #4 の巨大ロット（§3.5）は証拠金・ロスカット計算に直結。マージンモデルを持つなら反映必須。

### 6.1 集計指標の決定論的定義（MT5 標準レポート相当）

すべて**クローズ済みトレード**（決済確定）を対象とし、損益 `pᵢ` は**コスト控除後の純額**
（スプレッド/手数料は §2・§4 で確定したモデル反映後）とする。未決済ポジは集計対象外
（DD のみエクイティ系列で評価）。

**記号（一意）**
- `T = {p₁,…,p_N}`：クローズ済みトレードの純損益列。`N=|T|`（トレード数）。
- 勝ち `W={pᵢ>0}`、負け `L={pᵢ<0}`、同値 `B={pᵢ=0}`。`Nw=|W|, Nl=|L|, Nb=|B|`、`N=Nw+Nl+Nb`。
- 総利益 `GP = Σ_{pᵢ∈W} pᵢ ≥ 0`、総損失 `GL = Σ_{pᵢ∈L} |pᵢ| ≥ 0`。
- エクイティ系列 `E₀,E₁,…,E_M`（各評価ティック/足、§6 の equity）。`peak_t = max_{k≤t} E_k`。

| 指標 | 英名/略称 | 定義式 | 0除算・未定義時 |
|---|---|---|---|
| 総損益 | Net Profit | `GP − GL = Σ pᵢ` | — |
| 総利益 | Gross Profit | `GP` | — |
| 総損失 | Gross Loss | `GL` | — |
| トレード数 | Trades | `N` | — |
| 勝ち/負け数 | Won / Lost | `Nw / Nl`（同値 `Nb` は別計上） | — |
| 勝率 | Win Rate | `Nw / N`（同値は分母に含み、勝ちに含めない） | `N=0 → NaN` |
| プロフィットファクター | Profit Factor (PF) | `GP / GL` | `GL=0 → ∞（GP>0）／NaN（GP=0）` |
| 損益レシオ | Payoff Ratio | `(GP/Nw) / (GL/Nl)`（平均利益÷平均損失） | `Nw=0 or Nl=0 → NaN` |
| 期待利得 | Expected Payoff | `Σ pᵢ / N`（1トレード平均損益） | `N=0 → NaN` |
| 最大DD（額） | Max Drawdown | `max_t (peak_t − E_t)` | `M<1 → 0` |
| 最大DD（率） | Max Drawdown % | `max_t (peak_t − E_t)/peak_t × 100` | `peak_t=0 の項は除外` |
| リカバリーファクター | Recovery Factor | `Net Profit / 最大DD（額）` | `最大DD=0 → NaN` |
| シャープレシオ（年率） | Sharpe (ann.) | `mean(r)/std(r, ddof=1) × √A`、`r_t=(E_t−E_{t−1})/E_{t−1}` | `std=0 or 標本<2 → NaN` |
| 最大連勝 | Max Consecutive Wins | `pᵢ>0` が連続する最長ラン長（＋当該期間の損益和） | ラン無し → 0 |
| 最大連敗 | Max Consecutive Losses | `pᵢ<0` が連続する最長ラン長（＋当該期間の損益和） | ラン無し → 0 |
| 最大利益トレード | Largest Profit Trade | `max pᵢ` | `Nw=0 → NaN` |
| 最大損失トレード | Largest Loss Trade | `min pᵢ` | `Nl=0 → NaN` |

**確定事項（曖昧性排除）**
- 同値トレード（`pᵢ=0`）は `N` に含め、勝ち/負けには含めない。連勝・連敗のランは
  同値で**途切れる**（カウントをリセットする）。
- 0 除算は原則 **NaN**。PF のみ `GL=0 かつ GP>0` で `∞` 表示とする。集計表示では
  `—`/`∞`/`NaN` を文字列として明示する（数値0で代替しない）。
- シャープの年率係数 `A`＝1年あたり評価期間数（足ベース）。収益率系列 `r` の基準
  （エクイティ/残高・単純/対数・足/トレード単位）は §7 決定事項 #9 で固定する。
- DD はエクイティ系列基準。残高基準DDが必要なら同式の `E_t` を `balance_t` に置換する。

---

## 7. 決定論性チェックリスト（実装前に固定する選択肢）

再現結果がブレる「採用ルール未確定」箇所。Python 実装前に値を固定し本書へ追記する。

| # | 決定事項 | 既定推奨 | 影響 EA |
|---|---|---|---|
| 1 | ティックモデル（全ティック/OHLC4展開/始値のみ） | 全ティック→無ければ OHLC O→H→L→C | #1〜#4 |
| 2 | スプレッドモデル（固定/可変, Ask/Bid 配分） | `Ask=Bid+spread` 固定 | 全 |
| 3 | 同一足 SL/TP 両ヒット時の優先 | SL 優先（保守） | #1, #5 |
| 4 | 発注足と同一ティックでの SL/TP 監視可否 | 次ティック以降 | #1, #5 |
| 5 | OHLC 展開時の高安順序（O→H→L→C / O→L→H→C） | 始値が高安どちらに近いかで切替 | #1〜#4 |
| 6 | セッションカレンダーの定義 | ブローカー実カレンダー | #2 |
| 7 | `_Digits`（桁数）の固定値 | テスト銘柄で固定 | #5 |
| 8 | #4 巨大ロット/SYMBOL_SPREAD 定数の扱い | 原典踏襲 or 補正（要判断） | #4 |
| 9 | シャープ等の収益率系列の基準（§6.1） | エクイティ・単純収益率・足ベース | 全 |

---

## 8. 再現の優先順位（推奨実装順）

依存の少なさ・処理の単純さ順。BACKTEST_SPEC §6 と整合。

1. **#1 TC24051901**：毎ティック・固定SL/TP・状態変数なし。**エンジン骨格（§2/§4.1/§5/§6）の検証台**。
2. **#3 TC24051903**：符号＋反転決済。SL/TP 無し（§4.3 のみ）。エンジンの決済経路を検証。
3. **#5 PRO!fit_Band**：新規バー処理（§2-B）＋ADX 移植が前提。桁補正の検証。
4. **#2 TC24051902**：日付変更・予約執行・セッション（§1.1 カレンダー）が前提。
5. **#4**：BuyLimit（§4.2）＋タイマー＋Band ソース入手が前提。最後。
6. **#6**：対象外。

---

## 付録: 処理パターン別 EA 対応表

| 処理パターン | 該当 EA | 本書参照 |
|---|---|---|
| 毎ティック評価 | #1, #2, #3, #4 | §0.2, §2 |
| 新規バー1回評価 | #5 | §2-B, §3.4 |
| 状態変数で前値保持（static prev） | #2 | §3.2 |
| 日付変更トリガ執行 | #2 | §3.2 |
| セッション/時間帯フィルタ | #2 | §1.1, §3.2 |
| 成行注文 | #1, #2, #3, #5 | §4.1 |
| 指値（保留）注文＋失効 | #4 | §4.2 |
| 固定 SL/TP ヒット判定 | #1, #5 | §5 |
| 反転シグナルで決済 | #2, #3 | §4.3 |
| タイマーによる SL/TP 更新 | #4 | §3.5 |
| 桁数(_Digits)依存の SL/TP 補正 | #5 | §3.4 |
