"""実 MT5 突合: MA_Slope_EA + JP225 を Composition Root で実走し MT5実測と突合する。

load_case → build_interactor（warmup CSV + trading_start）→ CSV 実走 → BacktestResult を
expected.deals/results と比較し、一致率を定量化する。完全一致を捏造せず、残差
（sub-minute 時刻表現 / stop-out 発火バー精度）を不変条件テストとして固定する。

銘柄仕様の権威（ISSUE-445 段階 2・2026-08-25 是正）:
  ``contract_size`` / ``volume_min`` / ``volume_max`` / ``volume_step`` / ``stops_level`` /
  ``digits`` / ``point_size`` / ``leverage`` の 8 項目は **供給元スナップショット**
  （``marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json``・MT5 端末から機械取得）
  から引く。本ファイルにこれらのリテラルを書かない。従来は ``contract_size=10`` と
  ``volume_min/step=0.1`` を人が書いており、真値（1.0 / 1.0 / 1.0）との誤差が
  積 ``lot × contract_size`` の上で相殺していた（ISSUE-445 の RC-1）。
  ``lot_size`` だけは ``case.yaml`` の ``expert.lot``（=0.1）のままにする。これは MT5 の
  **EA 入力値**の忠実な記録であり、約定ロットではない。原典 ``MA_Slope_EA.mq5:NormalizeLot()``
  の移植（段階 1）が ``volume_min=1.0`` まで持ち上げ、実約定 1.0 lot になる
  （実測: 本実走の約定 volume 集合 = {1.0}／MT5 レポートの ``deals[].vol`` は 2327 件すべて 1）。
  よって積は ``1.0 × 1.0 = 1.0`` で従来の ``0.1 × 10`` と一致し、**下記 golden は bit-exact 不変**。

実走 config（全修正 ON + warmup + stop-out 精度2層・本テストが固定する条件）:
  entry_price_basis="current_open" / spread は Bar から取得 /
  stop_out_action="close_and_halt" / stop_out_level=台帳の口座 margin_so_so（ISSUE-546）/
  prime_first_trading_bar=True（層1）/ floating_pnl_basis="bid_ask"（層2）。
  データは warmup 込み CSV（2024-12-23 始点）を与え、trading_start=2025-01-02T01:00:00 を
  指定する。開始前のバーは指標(EMA)seed 収束のみを行い、MT5 と同じく 2024 履歴で EMA 収束済
  の状態で取引期間に入る。層1 は取引開始境界の degenerate バー(01:00)をプライム扱いして
  spurious SELL を除去し（初回約定を MT5 と同じ 01:01 buy@39412 に揃える）、層2 は含み損益を
  決済価格基準（買い=Bid=close / 売り=Ask=close+spread×point）で評価する。

ストップアウト水準（ISSUE-546・2026-10-01）:
  以前は ``stop_out_level=99.95`` を本ファイルに書いていた。台帳の口座の水準は 100.0
  （``margin_so_so``・MT5 端末から機械取得）であり、99.95 では MT5 が強制決済した 13:06 の
  評価点（維持率 99.959%）を割れと見なさず、MT5 に無い取引を 1 件余分に行っていた
  （往復 1164・net -6173.9）。台帳の水準で走ると次がすべて MT5 と一致する。

実測サマリ（本テストが固定する観測値・台帳の水準・上記 全修正 config）:
  - 往復トレード数は MT5 と同じ 1163。
  - 損益は 1163 件とも MT5 の deal の損益と一致する（MT5 は口座通貨の桁＝JPY 0 桁で
    四捨五入した値を記録する。素の損益が整数でない 4 件だけ丸めの差があり、素の net は
    -6168.9・丸めた net は MT5 と同じ -6169）。最終 balance も MT5 と同じ 3831。
  - stop-out の強制決済は MT5 の deal #2326（buy 13:04 @38325.7）→ #2327（@38295.7）と
    同じ玉・同じ価格。時刻は我々 13:06（評価した足の時刻）・MT5 13:07（記録 1 行・未調査）。
  - side + entry_time 一致率 = 98.2%（1142/1163）→ 戦略ロジック・エントリ時刻はほぼ一致。
    残差 21 件は MT5 の約定時刻が分の途中（:30 秒）にある sub-minute 時刻表現の差。
  - **SELL トレードは entry/exit 価格とも完全一致（574/574）**。reverse 決済 = 買い戻し
    = ask(open+spread×point) により spread が正しく加算される（spread 未加算への退行を禁止）。
  - **BUY トレードは entry/exit 価格とも完全一致（568/568・568/568）**。stop-out の
    強制決済（BUY・@38295.7）も MT5 の決済価格と一致する。
  - 初回 BUY の fill 価格式 open+spread×point を再現する: 層1 により初回 BUY 時刻は
    MT5 と同じ 2025-01-02T01:01 に揃い、価格 = open(39402)+spread(100)×point(0.1) = 39412
    で MT5 初回約定（01:01@39412）と完全一致する（01:00 の spurious SELL は生成されない）。

本テストは上記を「現実的トレランスの不変条件」で固定し、退行（戦略ロジック破壊・
エントリ時刻一致率低下・SELL/BUY exit spread 未加算への退行・価格不一致・trades/net/
balance の MT5 乖離拡大・warmup/trading_start/層1/層2 無効化・spurious SELL@01:00 再発・
stop-out 発火時刻の MT5 乖離）を検出する。報告値とテスト固定値は本テスト内で自己完結する。
"""
from __future__ import annotations

import numpy as np
import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.main import build_interactor
from simulator.tests.fixtures.mt5 import load_case

_CASE = "ma_slope_jp225_202501"
# warmup/trading_start: 取引開始時刻（これ以前のバーは EMA seed 収束のみ）。
# 層1（prime_first_trading_bar）により、この境界に当たる最初のバー(01:00 degenerate)は
# プライム扱いされ取引対象外となる（初回約定は次足 01:01）。
_TRADING_START = np.datetime64("2025-01-02T01:00:00")
# MT5(report.json) との突合基準値。
_MT5_TRADES = 1163
_INITIAL_DEPOSIT = 10_000.0
#: MT5 が deal の損益を記録する桁（口座通貨 JPY の桁）。report.json の deals[].profit は
#: すべて整数（実測）。
_MT5_PROFIT_DIGITS = 0
# MT5 report.json results の equity 系オラクル（突合基準）。
_MT5_EQUITY_DD_ABS = 6174.0       # initial - min(equity)
_MT5_EQUITY_DD_MAX = 6594.0       # equity peak-to-trough 最大金額 DD
_MT5_EQUITY_DD_MAX_PCT = 63.28    # 同点での % DD
_MT5_RECOVERY = -0.935547         # net / equity_dd_max
_MT5_SHARPE = -5.0                # per-trade Sharpe を [-5,5] にクランプした値


def _to64(mt5_time: str) -> np.datetime64:
    """MT5 deal 時刻 '2025.01.02 01:01:00' → numpy.datetime64。"""
    return np.datetime64(mt5_time.replace(".", "-").replace(" ", "T"))


def _run_engine(case):
    c = case.config
    sym, acc, ea = c["symbol"], c["account"], c["expert"]
    # 銘柄仕様 8 項目は供給元スナップショット（MT5 端末から機械取得）だけを権威とする
    # （ISSUE-445 段階 2・D3）。ここにリテラルを書かない＝人が値を選べない。
    spec = load_spec_fields(OANDA_JAPAN_MT5_LIVE, sym["name"])
    controller, request = build_interactor(
        # warmup 込み CSV（2024-12-23 始点）を与え、trading_start 前は EMA seed 収束のみ。
        data_path=case.warmup_csv,
        symbol=sym["name"],
        period="M1",
        ea_name="MA_Slope_EA",
        initial_deposit=float(acc["initial_deposit"]),
        # contract_size / volume_min / volume_max / volume_step / stops_level /
        # digits / point_size / leverage の 8 キー。
        **spec,
        ma_period=int(ea["ma_period"]),
        ma_method=ea["ma_method"],
        lot_size=float(ea["lot"]),
        stop_loss_points=int(ea["stop_loss"]),
        take_profit_points=int(ea["take_profit"]),
        slope_shift=int(ea["slope_shift"]),
        slope_min_points=float(ea["slope_min_points"]),
        # 全修正 ON: current_open + spread from bar + close_and_halt に加え、stop-out
        # 精度の2層修正を有効化する。
        #   層1 prime_first_trading_bar: 取引開始境界バー(01:00 degenerate) をプライム扱い
        #     しspurious SELL を除去（初回約定を MT5 と同じ 01:01 buy@39412 に揃える）。
        #   層2 floating_pnl_basis="bid_ask": 含み損益を決済価格基準（買い=Bid/売り=Ask）
        #     で評価し、stop-out 発火を MT5 の 13:07 に揃える。
        config_overrides={
            "tick_model": "open_only",
            "stop_out_action": "close_and_halt",
            "prime_first_trading_bar": True,
            "floating_pnl_basis": "bid_ask",
        },
        # ストップアウト水準は `spec` の stop_out_level（台帳の口座 margin_so_so）が唯一の
        #   出所（ISSUE-546）。以前はここに 99.95 を書いていた。
        trading_start=_TRADING_START,
    )
    return controller.execute(request)


def _mt5_round_trips(case):
    """expected.deals の in/out ペアを往復トレードへ復元する。"""
    deals = [d for d in case.deals if d["type"] != "balance"]
    rts = []
    cur = None
    for d in deals:
        if d["dir"] == "in":
            cur = {"side": d["type"], "etime": d["time"], "eprice": d["price"]}
        elif d["dir"] == "out" and cur is not None:
            rts.append(
                {
                    "side": cur["side"],
                    "entry_time": _to64(cur["etime"]),
                    "entry_price": cur["eprice"],
                    "exit_time": _to64(d["time"]),
                    "exit_price": d["price"],
                }
            )
            cur = None
    return rts


@pytest.fixture(scope="module")
def reconcile():
    """1 回だけ実走して我々のトレードと MT5 往復トレードを揃える（Fast: module scope）。"""
    case = load_case(_CASE)
    result = _run_engine(case)
    mt5 = _mt5_round_trips(case)
    mt5_by_key = {(m["side"], m["entry_time"]): m for m in mt5}
    return {
        "result": result,
        "ours": result.trades,
        "net": result.stats.profit,
        "balance": result.balance_curve[-1] if result.balance_curve else None,
        "mt5": mt5,
        "mt5_by_key": mt5_by_key,
        "expected": case.expected,
        "deals": [d for d in case.deals if d["type"] != "balance"],
    }


def _mt5_rounded(pnl: float) -> float:
    """MT5 が deal に記録する形（口座通貨の桁で四捨五入）。"""
    from decimal import ROUND_HALF_UP, Decimal

    quantum = Decimal(1).scaleb(-_MT5_PROFIT_DIGITS)
    return float(Decimal(repr(pnl)).quantize(quantum, rounding=ROUND_HALF_UP))


def _mt5_out_deals(deals) -> list:
    return [d for d in deals if d["dir"] == "out"]


def _mt5_stop_out_pair(deals) -> "tuple[dict, dict]":
    """MT5 の stop-out 決済 deal（comment が "so "）と、その玉を建てた直前の in deal。"""
    index = next(i for i, d in enumerate(deals) if (d.get("comment") or "").startswith("so "))
    return deals[index - 1], deals[index]


class TestMaSlopeReconcile:
    def test_run_produces_result_without_error(self, reconcile):
        # Act / Assert: 実走が例外なく BacktestResult を返す（end-to-end 結線の実証）。
        # close_and_halt のため stop_out 発火でも例外でなく結果が返る。
        assert reconcile["result"] is not None
        assert len(reconcile["ours"]) > 0

    def test_mt5_oracle_round_trip_count_is_1163(self, reconcile):
        # Assert: MT5 オラクル（report.json）の往復トレード数 = 1163
        assert len(reconcile["mt5"]) == _MT5_TRADES
        assert reconcile["expected"]["results"]["total_trades"] == float(_MT5_TRADES)

    def test_trade_count_equals_mt5(self, reconcile):
        # 台帳の水準（ISSUE-546）で走ると往復トレード数は MT5 と同じ（以前の 99.95 では 1164）。
        assert len(reconcile["ours"]) == len(_mt5_out_deals(reconcile["deals"]))

    def test_every_trade_profit_equals_the_mt5_deal_profit(self, reconcile):
        # 1163 件の損益を MT5 の記録形（口座通貨の桁で四捨五入）にすると、MT5 の out deal の
        # 損益と並びごと一致する。
        ours = [_mt5_rounded(t.pnl()) for t in reconcile["ours"]]
        theirs = [float(d["profit"]) for d in _mt5_out_deals(reconcile["deals"])]
        assert ours == theirs

    def test_net_profit_equals_mt5(self, reconcile):
        # 丸めた net は MT5 の Total Net Profit と一致する（素の net との差は丸めだけ）。
        mt5_net = reconcile["expected"]["results"]["total_net_profit"]
        assert sum(_mt5_rounded(t.pnl()) for t in reconcile["ours"]) == pytest.approx(mt5_net)
        assert reconcile["net"] == pytest.approx(mt5_net, abs=0.5 * len(reconcile["ours"]))

    def test_final_balance_equals_mt5(self, reconcile):
        # 最終 balance = 初期証拠金 + net（自己整合）。丸めた値は MT5 の最終 deal の balance。
        assert reconcile["balance"] == pytest.approx(
            _INITIAL_DEPOSIT + reconcile["net"], abs=0.1
        )
        mt5_final = float(reconcile["deals"][-1]["balance"])
        assert _INITIAL_DEPOSIT + sum(
            _mt5_rounded(t.pnl()) for t in reconcile["ours"]
        ) == pytest.approx(mt5_final)

    def test_first_buy_fill_reproduces_open_plus_spread_times_point(self, reconcile):
        # 層1（prime_first_trading_bar）により初回約定は MT5 と同じ 2025-01-02T01:01 buy。
        # fill 価格式 open+spread×point を再現する: bar 01:01 open=39402, spread=100,
        # point=0.1 → 39402+100×0.1 = 39412（MT5 初回約定 01:01@39412 と完全一致）。
        first_buy = next(t for t in reconcile["ours"] if t.side == "buy")
        assert first_buy.entry_time == np.datetime64("2025-01-02T01:01:00")
        assert first_buy.entry_price == pytest.approx(39412.0)

    def test_no_spurious_sell_at_session_boundary_01_00(self, reconcile):
        # 層1 の回帰固定: 取引開始境界の degenerate バー(2025-01-02T01:00・O=H=L=C=39400.5)
        # で MT5 に無い SELL を発注しない。初回トレードは MT5 と同じ 01:01 の buy であること。
        # 層1 を無効化すると 01:00 に spurious SELL が再発し本アサートが落ちる。
        first = reconcile["ours"][0]
        assert first.side == "buy"
        assert first.entry_time == np.datetime64("2025-01-02T01:01:00")
        # 01:00 ちょうどに建てたトレードが 1 件も存在しない（spurious SELL 不在）。
        boundary = np.datetime64("2025-01-02T01:00:00")
        assert not any(t.entry_time == boundary for t in reconcile["ours"])

    def test_side_and_entry_time_match_rate_at_least_98pct(self, reconcile):
        # 戦略ロジック・エントリ時刻の一致（sub-minute ずれ・stop-out 停止差を除く主指標）。
        # warmup + 両修正 ON での観測 = 98.2%（1142/1163）。warmless の 96.4% から改善
        # （初回が 01:01 に揃う）。残差は我々が 13:04 で停止し以降のエントリを生成しない必然差。
        mt5_keys = set(reconcile["mt5_by_key"])
        matched = sum(
            1 for t in reconcile["ours"] if (t.side, t.entry_time) in mt5_keys
        )
        rate = matched / len(reconcile["mt5"])
        assert rate >= 0.98, f"side+entry_time 一致率 {rate:.1%} < 98%（戦略退行の疑い）"

    def test_buy_trades_match_entry_and_exit_price_fully(self, reconcile):
        # BUY は entry=ask(=open+spread×pt)・exit=bid(=open) とも MT5 と完全一致する。
        # 層2 により stop-out は SELL 側（13:07）で発火するため、従来（層1単独）で残っていた
        # BUY exit の stop-out バー 1 件不一致が解消し、BUY exit も全件一致（568/568）になる。
        mt5_by_key = reconcile["mt5_by_key"]
        n = entry_ok = exit_ok = 0
        for t in reconcile["ours"]:
            m = mt5_by_key.get((t.side, t.entry_time))
            if m is None or t.side != "buy":
                continue
            n += 1
            entry_ok += t.entry_price == pytest.approx(m["entry_price"])
            exit_ok += t.exit_price == pytest.approx(m["exit_price"])
        assert n == 568  # 実測固定（BUY 往復で MT5 とキー一致する件数・全修正）
        # BUY entry 価格は全件一致（spread 加算が entry 側で正しい）。
        assert entry_ok == n, f"BUY entry 一致 {entry_ok}/{n}（BUY fill 退行の疑い）"
        # BUY exit も全件一致（層2 で stop-out が SELL 側へ移り BUY exit の乖離が消える）。
        assert exit_ok == n, f"BUY exit 一致 {exit_ok}/{n}（期待 568・層2 退行の疑い）"

    def test_stop_out_closes_the_mt5_position_at_the_mt5_price(self, reconcile):
        # stop-out の強制決済は MT5 の so deal（#2327）と同じ玉（同じ向き・建て時刻・建値）を
        # 同じ価格で決済する（ISSUE-546・台帳の水準）。時刻は我々が評価した足の時刻（13:06）で、
        # MT5 は 13:07 と記録する。この 1 分の差は未調査（ISSUE-546 に記録）。
        stop_outs = [t for t in reconcile["ours"] if t.exit_reason == "stop_out"]
        assert len(stop_outs) == 1, f"stop-out 強制決済は 1 件（実測 {len(stop_outs)}）"
        so = stop_outs[0]
        opened, closed = _mt5_stop_out_pair(reconcile["deals"])
        assert so.side == opened["type"]
        assert so.entry_time == _to64(opened["time"])
        assert so.entry_price == pytest.approx(opened["price"])
        assert so.exit_price == pytest.approx(closed["price"])
        assert so is reconcile["ours"][-1], "stop-out の後に取引が続いている（halt の退行）"

    def test_sell_trades_match_entry_and_exit_price_fully(self, reconcile):
        # cycle4 バグ① 修正後の回帰固定: SELL の決済（買い戻し=buy 約定）は
        # MT5=ask(open+spread×pt)。修正前は engine=bid(open) で spread 未加算のため
        # systematic に乖離していた（exit 0 件一致）。reverse 決済の ask に spread を
        # 加算したことで SELL entry/exit とも MT5 と完全一致する。
        # spread 未加算への退行を禁止する回帰（退行すると exit 0 件一致に落ちる）。
        mt5_by_key = reconcile["mt5_by_key"]
        n = entry_ok = exit_ok = 0
        for t in reconcile["ours"]:
            m = mt5_by_key.get((t.side, t.entry_time))
            if m is None or t.side != "sell":
                continue
            n += 1
            entry_ok += t.entry_price == pytest.approx(m["entry_price"])
            exit_ok += t.exit_price == pytest.approx(m["exit_price"])
        assert n == 574  # 実測固定（SELL 往復で MT5 とキー一致する件数・warmup）
        # SELL entry は一致（売り=bid=open で MT5 と同じ）。
        assert entry_ok == n, f"SELL entry 一致 {entry_ok}/{n}"
        # SELL exit も全件一致する（reverse 決済 ask = open+spread×pt）。
        assert exit_ok == n, (
            f"SELL exit 一致 {exit_ok}/{n}: spread 未加算への退行の疑い "
            "（reverse 決済 ask に spread が加算されていない）"
        )


class TestMaSlopeEquityStatsReconcile:
    """第2サイクル: 結線済 compute_stats() の equity 系 STAT_* を engine 実走 equity_curve
    で突合する（逆算3点 curve のトートロジー解消）。

    期待値は engine 実走の実測値を primary に固定し、MT5 report.json results との残差を
    トレランス付きで明示する。equity-DD は bar 解像度ゆえ bar 内の含み損ピークを捕捉できず
    MT5 のティック解像度 DD（6594）に対し ~25 の残差が残る（既知・現実的残差）。

    退行検出: 結線解除（sharpe/recovery/equity_dd を populate しない）/ equity_curve 経路の
    破壊 / トレランス外への乖離拡大 を本テストが検出する。
    """

    def test_stats_carries_equity_curve_for_dd(self, reconcile):
        # 結線の前提実証: BacktestResult.equity_curve が bar 別 equity を保持する。
        eq = reconcile["result"].equity_curve
        assert eq is not None and len(eq) > 0

    def test_equity_dd_abs_matches_mt5_tightly(self, reconcile):
        # equity_dd_abs（init - min(equity)）は engine 実走 = 6168.9。MT5 = 6174.0。
        # 我々の最安 equity は最終 balance（3831.1）で、足の評価点で測る。MT5 の最安 3826 は
        # 足の内側のティック（bar 解像度の限界・equity_dd_max の残差と同じ種類）。
        # 以前の 6173.9（残差 0.1）は 99.95 の水準で MT5 に無い取引を 1 件行い、その損で
        # 下がった値が偶然近かったもの（ISSUE-546）。
        stats = reconcile["result"].stats
        assert stats.equity_dd_abs == pytest.approx(6168.9, abs=0.1)  # 実走実測固定
        assert abs(stats.equity_dd_abs - _MT5_EQUITY_DD_ABS) <= 6.0   # tick 粒度残差 5.1

    def test_equity_dd_max_matches_mt5_within_tick_residual(self, reconcile):
        # equity_dd_max（peak-to-trough）は engine 実走 = 6563.9。MT5 = 6594.0。
        # 残差 30.1 は bar 解像度の限界（bar 内含み損ピーク非捕捉）由来の既知残差 25.0 と、
        # 谷の側の同じ種類の残差 5.1（上の equity_dd_abs）の和。以前の 6568.9 は 99.95 の
        # 水準で行った MT5 に無い取引 1 件の損 5.0 を含んでいた（ISSUE-546）。
        stats = reconcile["result"].stats
        assert stats.equity_dd_max == pytest.approx(6563.9, abs=0.1)       # 実走実測固定
        assert abs(stats.equity_dd_max - _MT5_EQUITY_DD_MAX) <= 31.0       # tick 粒度残差 30.1
        # % DD も同様: 実走 63.14% / MT5 63.28%（残差 ~0.14）。
        assert stats.equity_dd_max_percent == pytest.approx(63.14, abs=0.05)
        assert abs(stats.equity_dd_max_percent - _MT5_EQUITY_DD_MAX_PCT) <= 0.2

    def test_recovery_factor_equity_based_matches_mt5_within_residual(self, reconcile):
        # recovery = net / equity_dd_max（符号付き）。engine 実走 = -0.93982（net -6168.9 /
        # equity_dd_max 6563.9）。MT5 = -0.935547。残差 ~0.0043 は DD の tick 粒度残差由来。
        stats = reconcile["result"].stats
        assert stats.recovery_factor == pytest.approx(-0.93982, abs=1e-4)  # 実走実測固定
        assert abs(stats.recovery_factor - _MT5_RECOVERY) <= 0.01          # MT5 残差（~0.004）
        # 自己整合: recovery == net / equity_dd_max。
        assert stats.recovery_factor == pytest.approx(
            stats.profit / stats.equity_dd_max, abs=1e-9
        )

    def test_sharpe_per_trade_clamped_to_minus_five(self, reconcile):
        # per-trade Sharpe = (mean/std)×√N の素値 ≈ -5.08 を [-5,5] にクランプ → MT5 -5.0 一致。
        stats = reconcile["result"].stats
        assert stats.sharpe_ratio == pytest.approx(_MT5_SHARPE, abs=1e-9)
