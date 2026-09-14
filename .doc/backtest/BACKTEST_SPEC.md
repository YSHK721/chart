# バックテスト戦略ロジック仕様書（sample/MQL5 カスタムEA）

`sample/MQL5/Experts/` 配下のカスタム Expert Advisor（EA）と、それらが参照する
カスタム指標のコードを精査し、**バックテストで再現するための戦略ロジック**を決定論的に
記述する。本書は「誰が実装しても同じ売買が再現できる」水準（条件式・不等号・バッファ番号・
発注方式を一意に）を目標とする。

- **出典**: `sample/MQL5/`（移植元の MetaTrader 5 一式。リポジトリ追跡対象外）。
- **対象**: プロジェクト所有者のカスタムEA（標準サンプル Advisors/Examples/Free Robots は対象外）。
- **記述方針**: コードに書かれた事実のみ。推測は記さない。バグ・非決定要因は再現精度に直結する
  ため「⚠️」で明示する。
- **関連ドキュメント**:
  - 実行プロセス（OnTick の処理順）: [`./BACKTEST_PROCESS.md`](./BACKTEST_PROCESS.md)
  - 分析結果の用語・算出式: [`./BACKTEST_METRICS.md`](./BACKTEST_METRICS.md)
  - Python 設計仕様（9 項目）: [`./BACKTEST_DESIGN.md`](./BACKTEST_DESIGN.md)
  - 移植上の配列向き・型注意: [`./PORTING_GUIDE.md`](./PORTING_GUIDE.md)

---

## 0. 対象EA一覧と分類

| # | EA ファイル | 戦略の核 | エントリ | 決済 | 状態 |
|---|---|---|---|---|---|
| 1 | `TC24051901.mq5` | MADiff ゼロクロス | 両建て（買い/売り） | SL/TP（成行） | 実装済み |
| 2 | `TC24051902.mq5` | MADiff ゼロクロス | 買いのみ・翌日寄付 | 指標反転で買い手仕舞い | 実装済み（買い専） |
| 3 | `TC24051903.mq5` | MADiff 符号 | 買いのみ（売りは無効化） | 指標反転で全決済 | 実装済み（買い専） |
| 4 | `TC24051903_24052301.mq5` | MADiff + Band 四分位 | 買い指値（BuyLimit） | SL/TP＝Band値・タイマー管理 | 実装済み（要外部指標） |
| 5 | `PRO!fit_Band.mq5` | EMA傾き + ADX + DI | 両建て・新規バー1回 | SL/TP（成行・桁補正） | 実装済み |
| 5'| `my_first_ea.mq5` | （#5 と**完全同一コード**） | 同上 | 同上 | #5 のクローン |
| 6 | `range.mq5` | （ロジック未実装） | なし | なし | スケルトン |

**重要な事実**:
- #5 `PRO!fit_Band.mq5` と #5' `my_first_ea.mq5` は**バイト的にロジック同一**。両者とも内部名は
  `My_First_EA`（MetaQuotes 公式チュートリアル EA）を名乗る。ファイル名のみ異なるクローン。
- #6 `range.mq5` は `OnTick` が空のスケルトンで、単体では売買が発生しない。
- #1〜#4 は中核シグナルとしてカスタム指標 **MADiff** に依存する（§2）。#4 は加えて **Band**
  指標に依存するがソース不在（§5）。

---

## 1. 共通の前提・データモデル

バックテスト実装（Python 等）で全EAに共通して必要となる前提。

- **時系列の向き**: MQL のシリーズ配列は `[0]`＝最新足、`[1]`＝1本前。Python へ移すときは
  昇順（古い→新しい）へ読み替える（PORTING_GUIDE §4-3 と同じ注意）。
- **`_Point`**: 1ポイントの価格幅。`points × _Point` で価格差に変換する。
- **`_Digits`**: 価格の小数桁。5桁/3桁銘柄では一部EAが SL/TP を×10する（#5）。
- **約定価格**: 買い＝Ask、売り＝Bid。SL/TP も Ask/Bid 基準。**スプレッド込みのモデルが必須**。
- **新規バー判定**: 一部EAは「足が変わった最初のティックでのみ」処理する（#5）。それ以外は
  毎ティック評価（#1〜#4）。バックテストの足解像度（ティック/1分/始値のみ）で結果が変わる。

---

## 2. 中核シグナル源：MADiff 指標

`sample/MQL5/Indicators/MADiff.mq5`（内部ラベル `OpenClose MADiff`）。#1〜#4 が参照する。

| 項目 | 値 |
|---|---|
| 表示 | サブウィンドウ（`indicator_separate_window`）／ヒストグラム（赤） |
| バッファ | 1本のみ。`SetIndexBuffer(0, ExtMADiffBuffer, INDICATOR_DATA)` |
| input | `MAPeriod`(int, 既定 **14**)／`MAMethod`(ENUM_MA_METHOD, 既定 **MODE_SMA**) |
| 計算に使う MA | `iMA(NULL,0,MAPeriod,0,MAMethod,PRICE_OPEN)` と `iMA(...,PRICE_CLOSE)`（shift=0） |
| 描画開始 | `PLOT_DRAW_BEGIN = MAPeriod-1`／最小バー `rates_total ≥ MAPeriod-1` |

**バッファ0 の定義式（厳密）**:

```
MADiff[i] = MA(close, MAPeriod, MAMethod) − MA(open, MAPeriod, MAMethod)
```

同一バーの「終値の移動平均」と「始値の移動平均」の差。

**符号の意味（クロス判定に直結）**:
- `MADiff > 0`：終値MA > 始値MA → 期間平均で強気（買い圧力優勢）
- `MADiff < 0`：終値MA < 始値MA → 期間平均で弱気（売り圧力優勢）
- 負→正：強気転換、正→負：弱気転換

⚠️ **パラメータ既定値の不一致**: 指標自身の既定は `MAPeriod=14 / MODE_SMA`。一方 EA 側から
`iCustom("MADiff", MAPeriod, MAMethod)` で渡す既定は EA ごとに異なる（#1: 5/EMA, #2: 14/SMA,
#3: 5/EMA, #4: 無指定＝指標既定 14/SMA）。**EA が渡す値が優先**される。

---

## 3. 各EA仕様

各EAを「概要／入力／指標参照／エントリ／決済／発注／制限／実行頻度／再現注意」で記述する。

### 3.1 TC24051901.mq5 — MADiff ゼロクロス両建て

- **概要**: MADiff のゼロクロスで両方向にエントリ。固定 SL/TP 付き成行。
- **入力**: `LotSize=0.1`／`StopLoss=100`(points)／`TakeProfit=200`(points)／`MAPeriod=5`／
  `MAMethod=MODE_EMA`。
- **指標参照**: `iCustom(Symbol(),Period(),"MADiff",MAPeriod,MAMethod)`。
  `CopyBuffer(handle,0,0,2,buf)` → `curr=buf[0]`（現足）, `prev=buf[1]`（1本前）。
- **エントリ**:
  - 買い: `prev < 0 && curr > 0` かつ 同方向ポジ無し → 成行買い
  - 売り: `prev > 0 && curr < 0` かつ 同方向ポジ無し → 成行売り
- **決済**: 発注時の固定 SL/TP のみ（反転決済なし）。
  - 買い: `price=Ask`, `sl=price−StopLoss×_Point`, `tp=price+TakeProfit×_Point`
  - 売り: `price=Bid`, `sl=price+StopLoss×_Point`, `tp=price−TakeProfit×_Point`
- **発注**: `OrderSend`（`TRADE_ACTION_DEAL`）／`volume=LotSize`／`deviation=2`／`magic=0`／
  `comment="OpenClose_MADiff_EA"`。
- **制限**: `IsTradeOpen(type)` で同方向ポジの重複を禁止（反対方向は同時保有可）。1日上限なし。
- **実行頻度**: 毎ティック（新規バー判定なし）。
- **再現注意**: ⚠️ `IsTradeOpen` は `PositionGetInteger(POSITION_TYPE)` をポジ選択前に評価する
  曖昧実装。単一シンボル・少数ポジ前提なら実害は小さい。

### 3.2 TC24051902.mq5 — MADiff 買い専・翌日寄付（UTF-16）

- **概要**: MADiff が負→正でその日「予約」し、**翌日の最初**に寄付買い。指標が負転で買い手仕舞い。
- **入力**: `LotSize=0.01`／`MAPeriod=14`／`MAMethod=MODE_SMA`／`MaxTradesPerDay=3`／
  `CheckMarketHours=true`。
- **指標参照**: `iCustom("MADiff",MAPeriod,MAMethod)`。`CopyBuffer(handle,0,0,1,buf)` →
  `curr=buf[0]` のみ。`prev` は `static` 変数で前ティック値を保持。
- **エントリ（2段階）**:
  1. シグナル検出（買い予約）: `tradeCount < MaxTradesPerDay && (!CheckMarketHours || MarketIsOpen())`
     のもとで `curr > 0 && prev <= 0` → `pendingBuyOrder = true`
  2. 発注実行: **日付が変わった最初の評価**（`currentTime/86400 != lastTradeDay`）で
     `pendingBuyOrder && MarketIsOpen()` なら成行買い → `pendingBuyOrder=false`
- **決済**: 日付変更時に `curr < 0` なら買いポジを全クローズ（`ClosePositions(BUY)`）。固定 SL/TP は**なし**。
- **発注**: `OrderSend`／`volume=LotSize`／`deviation=2`／`magic=0`／SL・TP 指定なし。
- **制限**: `MaxTradesPerDay=3`／`MarketIsOpen()`（週末除外＋`SymbolInfoSessionTrade` のセッション
  0〜2 で現在時刻が取引時間内か判定）。
- **実行頻度**: 毎ティック評価だが、**発注は日付変更時のみ**（前日予約を翌日寄付で執行）。
- **再現注意**:
  - ⚠️ `tradeCount` を**インクリメントするコードが見当たらない**ため、`MaxTradesPerDay` 制限が
    実質効かない可能性。原典挙動として要再現確認。
  - ⚠️ 売り戦略は未実装（買い専）。
  - `MarketIsOpen()` はブローカーのセッション時間（`SymbolInfoSessionTrade`）に依存。バックテスト
    では同等のセッションカレンダーが必要。

### 3.3 TC24051903.mq5 — MADiff 符号・買い専・反転全決済

- **概要**: MADiff の符号で買い建てし、符号反転で手仕舞い。`CTrade` 使用。
- **入力**: `LotSize=0.01`（⚠️宣言のみ・未使用）／`MAPeriod=5`／`MAMethod=MODE_EMA`。
- **指標参照**: `iCustom("MADiff",MAPeriod,MAMethod)`。`CopyBuffer(handle,0,0,1,buf)` → `curr=buf[0]`。
- **エントリ**:
  - 買い: `curr > 0` かつ `PositionsTotal()==0` → `trade.Buy(1)`
  - 売り: `curr < 0` の分岐に `trade.Sell(1)` が**あるがコメントアウト**（発注されない）
- **決済**: 指標反転で全決済。`curr > 0` のとき保有売りを `PositionClose`、`curr < 0` のとき保有買いを
  `PositionClose`。固定 SL/TP は**なし**。
- **発注**: `CTrade.Buy(1)`（数量リテラル `1`。`LotSize` は不使用）／deviation・magic はデフォルト。
- **制限**: `PositionsTotal()==0` のときのみ新規買い。1日上限・時間帯フィルタなし。
- **実行頻度**: 毎ティック。
- **再現注意**: ⚠️ 数量が `LotSize` でなくリテラル `1` ロット。⚠️ 売り無効のため実質「買い専＋反転決済」。

### 3.4 TC24051903_24052301.mq5 — MADiff + Band 四分位の買い指値

- **概要**: MADiff と四分位バンド指標 `Band`（28バッファ）を時間枠毎に更新し、深い弱気
  （`MADiff < -1`）かつ価格が下側四分位を上回るとき **BuyLimit 指値**を発注。SL/TP は Band の
  四分位値を直接使用し、タイマーで建値管理する。
- **入力**: `LotSize`/`MAPeriod`/`MAMethod`（いずれも宣言のみ・**未使用**）。実効パラメータは
  `const`: `STOP_LOSS_PIPS=660`／`TAKE_PROFIT_PIPS=5000`／`CURRENT_BALANCE_STOP_LOSS_LEVEL=0.01`。
- **指標参照**:
  - `iCustom(NULL,0,"MADiff")`（無指定＝指標既定 14/SMA）→ `MADiff[0]`
  - `iCustom(NULL,0,"Band")` → 28バッファ（`pOL51`=buf13, `pOL98`=buf3, `pOL99`=buf1,
    `pOH99`=buf14 等。`p/n`＝正/負、`OH/OL`＝始値→高値/安値、数字＝四分位水準）
- **エントリ**（OnTick 直書き経路のみが実行される）:
  - 条件: `MADiff[0] < -1` かつ `pOL51 + SYMBOL_SPREAD < Bid` かつ `PositionsTotal()==0 && OrdersTotal()==0`
  - 発注: `trade.BuyLimit(volume, 指値=pOL98, NULL, SL=pOL99, TP=pOH99, ORDER_TIME_SPECIFIED, 期限)`
  - 期限: `orderTimeSpecified = iTime(_Symbol,_Period,0) + PeriodSeconds() − 60`
  - 売り: なし
- **決済**: SL=`pOL99`／TP=`pOH99`（**Band の絶対価格値を直接使用、_Point換算なし**）。
  `OnTimer()` が保有ポジの SL を建値、TP を `pOH99` に更新。
- **発注**: `CTrade.BuyLimit`。⚠️ `volume = (long)AccountInfoDouble(ACCOUNT_MARGIN_FREE)`（証拠金
  余剰額をそのままロット数に代入 → 非現実的に巨大なロットになり得る）。
- **制限**: `PositionsTotal()==0 && OrdersTotal()==0` のときのみ。指標更新は時間枠経過時
  （`ShouldUpdate()`）、発注評価は毎ティック。`EventSetTimer(PeriodSeconds()-60)`。
- **実行頻度**: 毎ティック（発注条件評価）＋時間枠毎（指標更新）＋タイマー（SL/TP管理）。
- **再現注意**:
  - ⚠️ **`SYMBOL_SPREAD` を関数でなく列挙定数のまま加算**している（`SymbolInfoInteger(...,SYMBOL_SPREAD)`
    でない）。実スプレッドでなく定数値が固定加算される原典バグ。再現時はこの定数挙動を踏襲するか要判断。
  - ⚠️ 多数のヘルパ関数（`IsConditionMet`/`CheckDeadCross`/`SetStopLossAndTakeProfit`/
    `PlaceBuyLimitOrder` 等）は定義のみで OnTick から未呼び出し。実行経路と混同しないこと。
  - **Band 指標のソースが無い**（§5）ため、`pOL*/pOH*` の算出式は本リポジトリから再現不可。

### 3.5 PRO!fit_Band.mq5 ＝ my_first_ea.mq5 — EMA傾き + ADX + DI

> 両ファイルは**ロジック完全同一**（MetaQuotes 公式 My_First_EA のクローン）。1仕様で両方を満たす。

- **概要**: EMA(8) の傾きと直前足終値の位置、ADX(8)>22、+DI/−DI の大小でトレンド方向を判定し、
  **新規バー1回**だけ成行で1ポジション売買。
- **入力**: `StopLoss=30`／`TakeProfit=100`／`ADX_Period=8`／`MA_Period=8`／`EA_Magic=12345`／
  `Adx_Min=22.0`／`Lot=0.1`。
- **指標参照**:
  - `iADX(NULL,0,ADX_Period)` → buf0=ADX本線, buf1=+DI, buf2=−DI（各 `CopyBuffer(...,0,3,...)`、3本）
  - `iMA(_Symbol,_Period,MA_Period,0,MODE_EMA,PRICE_CLOSE)` → buf0=EMA（3本）
  - 配列は `ArraySetAsSeries(true)`（`[0]`=現足, `[1]`=1本前, `[2]`=2本前）。`p_close = mrate[1].close`。
- **エントリ**（全条件 AND、厳密不等号）:
  - 買い: `EMA[0]>EMA[1] && EMA[1]>EMA[2]`（上昇）＆ `p_close>EMA[1]` ＆ `ADX[0]>Adx_Min` ＆ `+DI[0]>−DI[0]`
  - 売り: `EMA[0]<EMA[1] && EMA[1]<EMA[2]`（下降）＆ `p_close<EMA[1]` ＆ `ADX[0]>Adx_Min` ＆ `+DI[0]<−DI[0]`
- **決済**: 発注時の固定 SL/TP のみ。
  - 桁補正: `if(_Digits==5||_Digits==3){ STP=StopLoss*10; TKP=TakeProfit*10; }`
  - 買い: `sl=Ask−STP×_Point`, `tp=Ask+TKP×_Point`（`NormalizeDouble`）
  - 売り: `sl=Bid+STP×_Point`, `tp=Bid−TKP×_Point`
- **発注**: `OrderSend`／`volume=Lot`／`deviation=100`／`magic=EA_Magic`／`type_filling=ORDER_FILLING_FOK`／
  成功判定 `retcode∈{10009,10008}`。
- **制限**: `PositionSelect(_Symbol)` で保有方向を判定し同方向の重複を禁止（⚠️ **magic で絞っていない**ため
  同シンボルの他ポジも検知）。`Bars<60` は処理しない。
- **実行頻度**: **新規バーのみ**（`Old_Time != New_Time[0]` の最初のティックで `IsNewBar=true`、
  それ以外は return）。
- **再現注意**:
  - ⚠️ 判定に `[0]`（形成中の現足）の ADX/EMA を使用。新規バー直後のため前足確定値に近いが、厳密には
    現足インデックス。`p_close` のみ確定済み 1本前足。
  - ⚠️ `ORDER_FILLING_FOK` 固定／`_Digits` で SL・TP 幅が10倍変化／反対方向ポジの同時保有を完全には排除しない。

### 3.6 range.mq5 — スケルトン（売買なし）

- **概要**: `OnInit` で `iCustom("My_Indicator",ma_period)` のハンドルを生成するのみ。`OnTick`/`OnTrade`/
  `OnTester` は空。**売買ロジック未実装**。
- **入力**: `StopLoss=66`／`TakeProfit=132`／`Lot=0.1`（⚠️ `int` 型に `0.1` 代入＝**0 に切り捨て**）／
  `ma_period=20`。
- **再現注意**: 単体ではトレードが発生しない。再現には欠落している `My_Indicator` 実体（§5）と、未実装の
  `OnTick` ロジックの両方が必要。**現状はバックテスト対象にならない**。

---

## 4. ソース不在で再現不可な依存

以下はコンパイル済みのみ／完全不在のため、本リポジトリからは計算式を抽出できない。**再現には原典の
ソース入手が必須**。

| 依存 | 参照元EA | 状態 | 影響 |
|---|---|---|---|
| `Band` 指標（28バッファ四分位） | #4 TC24051903_24052301 | `Indicators/Band.ex5` のみ（.mq5 なし） | 指値価格・SL・TP（pOL/pOH）の算出式が不明 → #4 の完全再現不可 |
| `My_Indicator` | #6 range | ソース完全不在（参照は range.mq5 内のみ） | #6 の指標バッファ定義が不明（そもそも #6 は売買未実装） |

> 補足: #4 の `Band` は、本プロジェクトで移植済みの `indicators/profit_band`（PRO!fit_Band の値幅四分位
> バンド）と概念が酷似する。`pOL/pOH/数字(99/98/…)` の命名も profit_band の `{bucket}_{percent}` と対応
> しており、Band ≒ PRO!fit_Band 指標の可能性が高い。確証には原典ソース照合が必要。

---

## 5. バックテスト再現上の共通注意（バグ・非決定要因の総括）

再現結果が原典とズレる主因。Python 等で実装する際は、原典の挙動を**まず忠実再現**してから改善する
（PORTING_GUIDE §4-4 の方針）。

1. **約定/スプレッドモデル**: 全EAが買い=Ask・売り=Bid で発注し SL/TP も Ask/Bid 基準。ティック
   スプレッドの有無で約定・SL/TP ヒットが変わる。
2. **足解像度と実行頻度**: #1〜#4 は毎ティック評価、#5 は新規バー1回。バックテストのモデリング
   （全ティック/コントロールポイント/始値のみ）で結果が大きく変わる。
3. **MADiff の渡しパラメータ**: EA ごとに `MAPeriod/MAMethod` が異なる（§2 末尾）。指標既定値で
   なく EA が渡す値を使うこと。
4. **#2 の `tradeCount` 未加算**・**#3 のリテラル1ロット**・**#4 の証拠金全額ロット/`SYMBOL_SPREAD`
   定数加算**・**#6 の `Lot=int(0.1)=0`**：いずれも原典のコード事実。意図せぬ挙動だが再現対象。
5. **magic 未フィルタ**（#5）：同一シンボルの他ポジを誤検知し得る。単独運用前提なら影響軽微。
6. **`_Digits` 依存**（#5）：5桁/3桁銘柄で SL/TP が10倍。テスト銘柄の桁数を固定すること。
7. **セッション依存**（#2）：`SymbolInfoSessionTrade` の取引時間カレンダーが必要。
8. **外部指標ソース不在**（#4 Band, #6 My_Indicator）：§4 参照。

---

## 6. Python 再現に向けた最小データ要件・推奨

本プロジェクトの移植方針（`indicators/`＋`common/`）に沿って backtest を実装する場合の指針。

- **入力データ**: OHLC（昇順）＋ティック or Bid/Ask（スプレッド再現用）＋足の時刻。
- **再利用可能な部品**:
  - MADiff = `MA(close) − MA(open)`。`indicators/moving_averages`（移植済み）の各 MA 関数と
    `common.applied_price`（CLOSE/OPEN）で構成可能。
  - #5 の EMA は `indicators/moving_averages.exponential_ma`、ADX は別途移植が必要（未移植）。
  - #4 の Band は `indicators/profit_band` で代替可能か要照合（§4 補足）。
- **再現の優先順位（推奨）**: ①#1（最も素直なクロス＋SL/TP）→ ②#3 → ③#5（要 ADX 移植）→
  ④#2（セッション依存）→ ⑤#4（要 Band ソース）。#6 は対象外。

---

## 付録: 参照ファイル一覧

| 種別 | パス |
|---|---|
| EA #1 | `sample/MQL5/Experts/TC24051901.mq5` |
| EA #2 | `sample/MQL5/Experts/TC24051902.mq5`（UTF-16） |
| EA #3 | `sample/MQL5/Experts/TC24051903.mq5` |
| EA #4 | `sample/MQL5/Experts/TC24051903_24052301.mq5` |
| EA #5 | `sample/MQL5/Experts/PRO!fit_Band.mq5` |
| EA #5'| `sample/MQL5/Experts/my_first_ea.mq5`（#5 と同一） |
| EA #6 | `sample/MQL5/Experts/range.mq5`（スケルトン） |
| 指標 | `sample/MQL5/Indicators/MADiff.mq5` |
| 指標（不在） | `Band`（.ex5 のみ）／`My_Indicator`（完全不在） |
