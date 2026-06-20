# バックテスト 実 MT5 突合 結果記録

JP225 `MA_Slope_EA`（`260618-01.ex5`）を実 MT5 Strategy Tester（OANDA-Japan MT5 Live）と
数値突合した結果の記録。突合の目的は、本バックテストエンジン（`backtest/`）の確定トレード・
損益・残高・stop-out を実 MT5 と一致させ、エンジンの正しさを実証すること。

最終到達点：**2026-01 / 2026-02 の両月を、単一の config で実 MT5 と literal bit-exact
（trades・全建値・全決済・net・balance まで完全一致）に到達**。

---

## 1. 突合結果一覧

| ケース | modelling | 期間 | trades | net (ours / MT5) | balance (ours / MT5) | stop-out | 一致度 |
|---|---|---|---|---|---|---|---|
| 2026-01 | every-tick / 1分OHLC | 2026.01 | 1444 / 1444 | −4649.0 / −4649 | 5351.0 / 5351 | 01-14 | **literal bit-exact**（0/1444 不一致） |
| 2026-02 (run5) | 1分OHLC | 2026.02 | 886 / 886 | −5021.0 / −5021 | 4979.0 / 4979 | 02-09 | **literal bit-exact**（0/886 不一致） |
| 2025-01 | 1分OHLC | 2025.01 | 1164 | −6173.9 / −6169 | 3826.1 / 3831 | — | near（残差 ~5・既知。warmup/層補正の旧突合） |

- 2026-01 は MT5 で every-tick と 1分OHLC の両方を実行したが、本 EA は成行をバー open で約定する
  ため両 modelling で MT5 結果は同一（balance 5351）。Delays（50ms / 0）も結果に非影響。
- 数値型はすべて double（IEEE 754 binary64＝Python float）。`profit_round_digits=0` により各約定
  損益を整数値 double へ丸めるため、net/balance の `.0` は端数なしの真の整数（MT5 の JPY 整数表示と
  数値一致）。

---

## 2. literal bit-exact を得る config

`backtest/main/build_interactor` への指定（JP225 econ + MA_Slope）：

```python
build_interactor(
    data_path="<JP225_M1_*.csv（MT5 export・タブ区切り）>",
    symbol="JP225", period="M1", ea_name="MA_Slope_EA",
    initial_deposit=10000.0, contract_size=10.0,
    volume_min=0.01, volume_max=100.0, volume_step=0.01,
    stops_level=0, digits=1, point_size=0.1, leverage=10.0,
    ma_period=20, ma_method="ema", lot_size=0.1,
    stop_loss_points=0, take_profit_points=0,
    slope_shift=1, slope_min_points=1.0,
    stop_out_level=100.0,
    trading_start=pd.Timestamp("<対象月初日>"),  # 2026-01-01 / 2026-02-01
    config_overrides={
        "tick_model": "ohlc_expand",        # 1分OHLC（every-tick 検証時は "real_ticks"）
        "entry_price_basis": "current_open", # 成行＝バー open クォート（買い=open+spread×point/売り=open）
        "floating_pnl_basis": "bid_ask",     # 含み損＝買い Bid/売り Ask
        "stop_out_action": "close_and_halt", # 強制決済して完走
        "session_calendar": "jp225",         # 日次セッション [01:01, 23:58]
        "profit_round_digits": 0,            # 約定損益を口座通貨(JPY=0桁)へ丸め
        "stop_out_at_open": True,            # stop-out をバー open でも先行評価
    },
)
```

- M1 データは対象月の前から供給（warmup）。例：2026-02 は 2026-01-26 起点で 02-01 前を EMA seed に充当。
- 既定（config_overrides 無指定）では上記 knob はすべて従来挙動（byte-identical）。突合時のみ有効化する。

---

## 3. 一致に効いた要因（ISSUE-017〜022）

実 MT5 と一致させるために特定・是正した 6 因子。いずれも config gated で、既定は従来挙動を 1 バイトも
変えない（byte-identical）。

| # | 因子 | 内容 | 一次証拠 |
|---|---|---|---|
| ISSUE-017 | every-tick 建値 | every-tick 成行を「足内初回ティック」→「バー open クォート」約定へ是正（bar-mode と同一） | 初回トレード価格・stop-out 日・trades の収束 |
| ISSUE-018 | セッションカレンダー | 市場閉鎖時間帯の成行を拒否（MT5 は `[market closed]` で拒否し開場バーで約定）。MaSlope は保有側 level-trigger のため約定抑止のみで次開場バーに自動再発注 | journal `failed market ... [market closed]` |
| ISSUE-019 | stop-out 決済価格 | 強制決済を「margin 割れ判定時点の現値（mark_price）」へ。成行建値用 open クォートの流用（始値約定）を是正 | 2026-01 最終 stop-out 53859.2 |
| ISSUE-020 | 約定損益の通貨丸め | 約定損益を口座通貨精度（JPY=0桁・half-away-from-zero）へ丸めて balance/stats 反映 | MT5 全約定 profit が整数・残差0.2の2件 |
| ISSUE-021 | カレンダー日次クローズ | 「金曜23:55」過剰適合を「日次 23:59 クローズ（毎日・[01:01,23:58]）」へ是正 | journal `02-06 23:59 [market closed]`・23:59約定0件/23:58約定2件 |
| ISSUE-022 | stop-out をバー open 先行評価 | 実 MT5 OHLC は O→H→L→C の最初(open)で margin 評価。週末ギャップで open が割れた玉を open クォートで決済 | 2026-02 stop-out 57657=open+spread |

詳細・検証は `ISSUE.md` の各エントリ参照。

---

## 4. セッション境界（JP225・実 MT5 突合で確定）

`Jp225SessionCalendar`（`session_calendar="jp225"`）の tradeable 窓 = **[01:01, 23:58]**（毎日同一）。
- 日次プレオープン：00:00–01:00（0..60分）は閉鎖、01:01 開場。
- 日次クローズ：23:59（1439分）は閉鎖、23:58 まで開場。
- 土日はバー自体が存在しないため明示判定不要。

閉鎖バーでは新規成行（ドテン reverse 含む）を約定しない。保有は不変のため戦略が次開場バーで再発注し、
実 MT5 の「fail→retry→開場約定」を再現する。

---

## 5. 再現方法

- **committed オラクル fixture**：`backtest/tests/fixtures/mt5/ma_slope_jp225_202601/expected/report.json`
  （2026-01）、`.../ma_slope_jp225_202501/`（2025-01）。
- **回帰テスト**：`backtest/tests/unit/test_run_backtest.py`（stop-out・通貨丸め）、
  `test_session_calendar.py`（カレンダー）、`test_run_backtest_every_tick.py`（every-tick 建値）、
  integration `test_ma_slope_reconcile.py`（2025-01 engine 実走突合）。
- **使い捨て突合スクリプト・生データ**：`backtest/tests/confirmation/<日付>/`（.gitignore・大容量）。
  各 `_reconcile_*.py` が当該月の M1/journal を読み、ours と MT5 オラクルを突合する。

---

## 6. 既知の限界

- **every-tick（real_ticks）経路**：MT5 輸出ティック CSV は片側更新（bid または ask が NaN）のため
  ffill でクォート復元する。これは MT5 内部の実ティック列と微差を生み、stop-out 境界で ±数件・~数十pt の
  残差を残し得る（ISSUE-017 残差）。**1分OHLC modelling では本差は発生せず literal bit-exact**。
- **equity 系 stats（equity-DD max 等）**：bar 解像度では MT5 のティック解像度ピークに届かない構造的残差
  （ISSUE-013）。確定トレード・balance 系は一致。
- セッション境界・建値・stop-out の各規則は実測突合（2025-01 / 2026-01 / 2026-02）に基づく。別シンボル・
  別ブローカー・祝日等の異なる条件では再検証が必要。
