# バックテスト 実 MT5 突合（指値/逆指値・1分OHLC 挙動）結果記録

JP225 `MA_Slope_Pending_EA`（`260620-01_limit_stop.ex5`）を実 MT5 Strategy Tester
（OANDA-Japan MT5 Live・1 分 OHLC modelling）と数値突合した結果の記録。
成行版の記録は [`BACKTEST_MT5_RECONCILIATION.md`](./BACKTEST_MT5_RECONCILIATION.md) を参照。

本記録の主眼は、**指値（limit）注文＋SL/TP＋ドテン反転**という「足の途中で約定・決済が
連鎖する」EA を、MT5 が OHLC（4 本値）だけからバー内の時系列をどう疑似再現するかを解明し、
そのルールをエンジンへ移植して **literal bit-exact** を達成したことの記録である。

最終到達点：**2026-03 指値 RUN2（Lot0.1 / TP500）の全 12787 トレードを、entry/exit/profit・
件数・最終 balance まで実 MT5 と完全一致（0/12787 不一致）**。さらに **独立した別月
2026-04（同一パラメータ・全 1770 トレード）でも追試し、entry/exit/profit・件数・最終 balance
まで bit-exact 一致（0/1770 不一致）** を確認済み — 過剰適合でなく汎用に再現することの裏付け。

---

## 1. 結論（一目で確認）

| ケース | 判定 | 根拠 |
|---|---|---|
| **2026-03 指値 RUN2**（Lot0.1 / TP500） | ✅ **完全一致** | 全 12787 トレードの entry/exit/profit・件数・最終 balance 15666 が bit-exact（0/12787 不一致） |
| **2026-04 指値**（Lot0.1 / TP500・同一パラメータ） | ✅ **完全一致** | 全 1770 トレードの entry/exit/profit・件数・最終 balance 5390（Net −4610）が bit-exact（0/1770 不一致）。別月での独立追試 |
| 2026-03 指値 RUN1（Lot1.0 / TP400） | △ 未一致 | 指数 CFD の証拠金モデル差で大ロットが即 stop-out（pending ロジックと無関係・別件） |
| 逆指値**エントリー**（EntryType=2） | ― 未検証 | エンジンは `entry_type="stop"` で対応済だが当ディレクトリにオラクル無し。**SL/TP の逆指値的決済とは別物**（下記注記参照） |

> bit-exact オラクルは `ReportTester-900005560_2603-01.xlsx`（= RUN2）/
> `ReportTester-900005560_2604-01.xlsx`（= 2026-04）の Deals テーブル
> （in/out deal の price/profit/balance）。RUN1 は journal の最終 balance のみ。

### 突合パラメータ（RUN2）

```
EntryType=1(指値) / EntryOffsetPts=50 / MA_Period=60 / MA_Method=EMA / MA_Price=Close /
SlopeShift=1 / SlopeMinPts=1.0 / Lot=0.1 / StopLoss=200 / TakeProfit=500 /
initial 10000 JPY / leverage 1:10 / 期間 2026.03.01–2026.03.31 / JP225 1分OHLC
（M1 データは 2026-02-23 起点で供給し 03-01 前を warmup）
```

再現スクリプト：`backtest/tests/confirmation/260620-2603-01/_reconcile_2603_01.py`
（2026-04 追試は `backtest/tests/confirmation/260620-2604-01/_reconcile_2604_01.py`。
いずれもリポジトリルートで `PYTHONPATH=. python3 …` 実行）

### 注記：「SL/TP の逆指値的決済」と「逆指値エントリー」は別物

「指値買いなら TP＝指値売り・SL＝逆指値売りの注文を出している」という直感は **決済挙動としては
正しいが、実装上は別経路**であり、上表の「逆指値エントリー 未検証」とは指す対象が異なる。

- **SL/TP は独立注文ではなくポジション属性**。MT5 でも板に逆指値売り注文として並ばず、
  ポジションの S/L・T/P 価格レベルとして保持され、決済 deal の理由が `sl`/`tp` になるだけ。
  エンジンも同様で、`ma_slope_pending.py::_calc_sltp` が `Order.sl/tp` に載せ、
  `_execution.py::check_sltp_hit`（high/low クロス判定）が監視する。
- **下抜け／上抜けトリガーの価格判定は検証済み**。買い保有の SL は `low<=sl`（下落＝逆指値売り的）、
  TP は `high>=tp`（上昇＝指値売り的）で発火する。これは 2026-03/04 の bit-exact 突合で
  数千件が一致しており、**トリガー方向の判定ロジック自体は実証済み**。
- **未検証なのは「エントリー方式が逆指値」**（EntryType=2）。価格ブレイクで建てる
  `buy_stop`/`sell_stop` は `_execution.py::fill_pending_order`（lines 116–119・
  `Ask>=price` / `Bid<=price`）という **SL/TP 監視とは別関数**を通り、指値テストでは一度も
  呼ばれない。この建玉トリガー経路のみ MT5 オラクルが無く未実証。

> まとめ：**決済側の逆指値的トリガーは検証済み／建玉側の逆指値エントリー経路のみ未検証**。
> 後者の実証には EntryType=2 で実行した MT5 Strategy Tester レポートが要る。

---

## 2. MT5「1 分 OHLC」モードのバー内挙動 6 要因

MT5 は実ティックを使わない「1 分 OHLC」modelling で、**1 本の M1 足を
始値(O)→高値(H)→安値(L)→終値(C) の最大 4 つの疑似ティックに展開**し、その並びで
約定・SL/TP を判定する。同じ OHLC でも「H と L のどちらを先に通るか」「本当に 4 点なのか」が
結果を左右し、これらを正確に真似ないと決済タイミングが数足ずれて雪崩式に分岐する。

突合が合わないときは、概ねこの順に疑う。

### ① バー内の通過順（ohlc_order = "auto"）

同じ OHLC でも H 先 / L 先で、SL（上）と TP（下）のどちらに先に当たるかが変わる。
非ドジ足は **当該足の方向** で決まる：

- **強気足（close > open）→ 安値を先に通る O→L→H→C**
- **弱気足（close < open）→ 高値を先に通る O→H→L→C**

> 直感：実体の方向と逆の極値へ先に振れてから引ける（陽線は一旦下げてから上げて陽線で終わる）。

実例：bar 01:45（強気・O=57743.7 C=57763.7）は O→L→H→C。指値約定@H の後 C で SL 判定。

### ② ドジ足（close == open）は「前の足の方向」に従う

①で決められない引き分け（始値＝終値）は、**直前足のモメンタムを継続**する：

- 前足が陽線（prev_close > prev_open）→ **高値を先に通る**
- 前足が陰線（prev_close < prev_open）→ **安値を先に通る**

> 構造がほぼ同一の 2 つのドジ足が逆順になり当初は不可解だったが、**順序が結果を左右した
> ドジ足 8/8 がすべて前足方向と一致**。直前の勢いを引き継ぐ自然な挙動。

実例：bar 11:37（ドジ・前足陽）→ 高値先。bar 04:25（ドジ・前足陰）→ 安値先。

### ③ 生成ティック数 = min(tickvol, 4)

薄い足は 4 本の疑似ティックを作らない。生成本数は当該足の tick volume（`<TICKVOL>`）に依存：

- **tickvol ≥ 4 → 4 本フル**（O,H,L,C。等値の隣接も別ティックとして保持）
- **tickvol < 4 → 隣接等値を集約**して ≈ tickvol 本

> なぜ効くか：「約定した瞬間の足にもう次のティックが無い」と、その足では SL/TP に当たらず
> **次の足へ持ち越し**になる。薄い足ほど発生し、決済が 1〜数足ずれる。

実例：bar 23:12（tickvol=2・O=L, H=C）は 2 ティック → 約定@H 後ティック無し → 持ち越し。
bar 15:32（tickvol=39・L=C）は 4 ティック → 約定@L→C で SL（同足決済）。

### ④ 約定したティック自身では SL/TP を判定しない

ペンディングが約定した「そのコントロールポイント」では決済を見ない。**約定の次のティック
以降**で初めて SL/TP を監視する（実 MT5 のサーバはまず約定を処理し、決済は後続ティック）。

> 結果として「同じ足の中で 約定→SL/TP 決済」も「次の足へ持ち越し」も両方あり得る。
> 成行の「建てた足は次 tick まで監視外」とは別ルール（成行は足全体スキップ、pending は
> 約定ティックの序数より後のみ監視）。

実例：bar 01:01 で約定@H(01:01:40)→後続 C(01:01:59)で SL 発火＝同足決済。

### ⑤ SL/TP の判定価格は「決済する側のレート」

スプレッドの当て方。決済は反対サイドのクォートで判定する：

- **売り保有の SL/TP → Ask（= Bid + spread×point）で判定**
- **買い保有の SL/TP → Bid で判定**

実例：高値=58078.7、売り玉 SL=58088.7。高値（Bid）だけ見ると未達だが
Ask = 58078.7 + spread100×0.1 = **ちょうど 58088.7** で約定。Bid 基準だと 1 足遅れていた。

### ⑥ 処理順は「サーバ（板）→ EA（戦略）」

各足の先頭ティック（open）で、**まずサーバが「前足から残存するペンディングの約定」と
「保有玉の SL/TP」を処理し、その後に EA の OnTick（on_new_bar）が走る**。これにより：

- **前の足で置いた指値が、次の足の始値で約定し得る**（EA が取り消す前にサーバが評価）
- 足の始値で保有玉が SL/TP に当たって flat になった直後、**同じ足で EA が新規発注できる**

> これを真似ないと、戦略の判断が常に 1 ティック早く、発注が 1 足遅れて全体がずれる。

実例：bar 23:49 の指値が bar 23:50 の始値で約定。bar 04:27 の保有玉が始値で SL→同足で次玉発注。

### （おまけ）テスト終端の清算

1 分 OHLC 固有ではないが、**期間終了時に残存建玉を最終足の close クォートで強制決済**する
（buy=Bid=close / sell=Ask=close+spread×point）。これで最後の 1 トレードが埋まり完全一致。

---

## 3. 覚え方（早見表）

| # | 要因 | 一言 | コード |
|---|---|---|---|
| ① | バー内順序 | 陽線=安値先 / 陰線=高値先 | `tick_model._ordered_ohlc_prices`（"auto"） |
| ② | ドジの順序 | 前足の勢いを継続 | 同上（prev_open/prev_close 保持） |
| ③ | ティック本数 | min(tickvol, 4)・薄い足は持ち越す | `OhlcExpandTickModel.ticks_of`（tickvol<4 で隣接集約） |
| ④ | 約定足の SL/TP | 約定ティックでは見ない、次から | `run_backtest` `opened_tick_ordinal` |
| ⑤ | 判定レート | 売り=Ask / 買い=Bid | `run_backtest` `q_ask/q_bid` 側別判定 |
| ⑥ | 処理順 | サーバ→EA（前足指値が始値で約定） | `run_backtest` open-tick 約定/SLTP を on_new_bar 前に処理 |

---

## 4. 設計方針（既定経路を壊さない）

すべての挙動は config-gated で、**既定値では 1 バイトも従来出力を変えない**：

- `pending_lifecycle: bool = False` — True で execute() を every-tick 経路へ振り向け、
  指値/逆指値のライフサイクル（残存ペンディング・ドテン・約定足 SL/TP）を有効化。
- `ohlc_order: "auto"` — `OhlcExpandTickModel(order="auto")` でバー内順序則＋tickvol ティック数。
  既定 `"ohlc"` は従来どおり O→H→L→C 固定・4 本（dedup なし）。
- 既存の成行 EA（`MA_Slope_EA`）は **bar-mode `execute()`** を通り tick_model を使わないため、
  本変更の影響を受けない。**成行 2026-01/02/03 の bit-exact 突合は維持（回帰なし）**。

### 主な変更ファイル

| 層 | ファイル | 変更 |
|---|---|---|
| domain | `order.py` | kind に sell_limit/buy_stop/sell_stop 追加（成行・buy_limit 不変） |
| domain | `trade_record.py` | exit_reason に `end_of_test` 追加 |
| usecase | `_execution.py` | `fill_pending_order`（4 種トリガ）追加 |
| usecase | `run_backtest.py` | pending ライフサイクルを `_execute_every_tick` に加算配線 |
| adapter | `execution/tick_model.py` | `OhlcExpandTickModel(order=...)`・"auto" 順序則・tickvol ティック数 |
| adapter | `strategy/ma_slope_pending.py` | **新規** MaSlopePending（成行 ma_slope.py は無変更） |
| framework/main | `config_loader.py`・`models.py`・`main/__init__.py` | `pending_lifecycle`・`entry_type`/`entry_offset_points` 配線 |

---

## 5. 突合の進め方（再現メモ）

- **in/out deal をペア化し entry/exit 価格で forward-align** して最初の分岐点を特定する。
  本 EA は -20(SL) が連続するため、profit/balance だけの集計照合は偽陽性になる（同値の
  -20 が並ぶと「一致」に見える）。entry/exit 価格での逐次照合が必須。
- 分岐したら、その足の OHLC・tickvol・spread と MT5 journal/xlsx の deal 時刻（:20/:40/:59 等の
  疑似ティック時刻）を突き合わせ、上記 6 要因のどれが効いているかを切り分ける。
- 1 つ直すと次の分岐が現れる。①順序 → ④約定足 SL/TP → ⑤Ask/Bid → ⑥処理順 → ③tickvol →
  ②ドジ → 終端清算、の順で 9 → 26 → 124 → 559 → 905 → 1466 → 2011 → 12786 → 12787 件と
  一致範囲が伸びていった。

> 関連: メモリ [[mt5-pending-order-reconciliation]] / [[mt5-reconciliation-factors]]
