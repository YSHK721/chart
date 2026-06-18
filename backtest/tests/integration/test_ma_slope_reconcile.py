"""実 MT5 突合: MA_Slope_EA + JP225 を Composition Root で実走し MT5実測と突合する。

load_case → build_interactor（warmup CSV + trading_start）→ CSV 実走 → BacktestResult を
expected.deals/results と比較し、一致率を定量化する。完全一致を捏造せず、残差
（sub-minute 時刻表現 / stop-out 発火バー精度）を不変条件テストとして固定する。

実走 config（全修正 ON + warmup + stop-out 精度2層・本テストが固定する条件）:
  entry_price_basis="current_open" / spread は Bar から取得 /
  stop_out_action="close_and_halt" / stop_out_level=99.95 /
  prime_first_trading_bar=True（層1）/ floating_pnl_basis="bid_ask"（層2）。
  データは warmup 込み CSV（2024-12-23 始点）を与え、trading_start=2025-01-02T01:00:00 を
  指定する。開始前のバーは指標(EMA)seed 収束のみを行い、MT5 と同じく 2024 履歴で EMA 収束済
  の状態で取引期間に入る。層1 は取引開始境界の degenerate バー(01:00)をプライム扱いして
  spurious SELL を除去し（初回約定を MT5 と同じ 01:01 buy@39412 に揃える）、層2 は含み損益を
  決済価格基準（買い=Bid=close / 売り=Ask=close+spread×point）で評価して stop-out 発火を
  MT5 の 13:07 に揃える。

実測サマリ（本テストが固定する観測値・2026-06 実走・上記 全修正 config）:
  - 我々の往復トレード = 1164、MT5 = 1163。差 ≈ 1 は sub-minute 時刻表現の残差。
  - net profit = -6173.9（MT5 = -6169.0）/ 最終 balance = 3826.1（MT5 = 3831.0・
    初期証拠金 10000）。差 ≈ 4.9 はトレード差＋stop-out 価格差由来の現実的残差。
    注: 層1 単独なら net/balance は MT5 にほぼ bit-exact（-6168.9 / 3831.1）だが stop-out
    発火が 13:16 とずれる。層2 を加えると stop-out が 13:07（MT5 一致）に揃う代わりに
    net/balance が ≈4.9 乖離する（stop-out 時刻一致 vs net/balance bit-exact のトレードオフ。
    本テストは「stop-out 時刻 = MT5 と完全一致」を優先する config を固定する）。
  - side + entry_time 一致率 = 98.2%（1142/1163）→ 戦略ロジック・エントリ時刻はほぼ一致。
    残差は我々が 13:07 stop-out で停止し以降のエントリを生成しない必然差。
  - **SELL トレードは entry/exit 価格とも完全一致（574/574）**。reverse 決済 = 買い戻し
    = ask(open+spread×point) により spread が正しく加算される（spread 未加算への退行を禁止）。
  - **BUY トレードは entry/exit 価格とも完全一致（568/568・568/568）**。層2 により
    stop-out が SELL 側（13:07）で発火するため、BUY exit の stop-out バー不一致が解消する
    （従来の層1単独では BUY が 13:04/13:16 で停止し 1 件不一致だった）。
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

from backtest.main import build_interactor
from backtest.tests.fixtures.mt5 import load_case

_CASE = "ma_slope_jp225_202501"
# warmup/trading_start: 取引開始時刻（これ以前のバーは EMA seed 収束のみ）。
# 層1（prime_first_trading_bar）により、この境界に当たる最初のバー(01:00 degenerate)は
# プライム扱いされ取引対象外となる（初回約定は次足 01:01）。
_TRADING_START = np.datetime64("2025-01-02T01:00:00")
# 我々の stop-out 強制決済バー。層2（bid_ask 含み損評価）により stop-out 発火が MT5 の
# 13:07 に一致する。この時刻に SELL を建てた直後の同バーで強制決済される（entry=exit=13:07）。
_OUR_STOPOUT_ENTRY = np.datetime64("2025-01-13T13:07:00")
# MT5(report.json) との突合基準値（実測の現実的トレランスで固定する）。
_MT5_TRADES = 1163
_MT5_NET = -6169.0
_MT5_BALANCE = 3831.0  # 初期 10000 + net(-6169)
_INITIAL_DEPOSIT = 10_000.0


def _to64(mt5_time: str) -> np.datetime64:
    """MT5 deal 時刻 '2025.01.02 01:01:00' → numpy.datetime64。"""
    return np.datetime64(mt5_time.replace(".", "-").replace(" ", "T"))


def _run_engine(case):
    c = case.config
    sym, acc, ea = c["symbol"], c["account"], c["expert"]
    controller, request = build_interactor(
        # warmup 込み CSV（2024-12-23 始点）を与え、trading_start 前は EMA seed 収束のみ。
        data_path=case.warmup_csv,
        symbol=sym["name"],
        period="M1",
        ea_name="MA_Slope_EA",
        initial_deposit=float(acc["initial_deposit"]),
        contract_size=float(sym["contract_size"]),
        volume_min=0.1,
        volume_max=100.0,
        volume_step=0.1,
        stops_level=0,
        digits=int(sym["digits"]),
        point_size=float(sym["point_size"]),
        leverage=float(sym["leverage"]),
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
            "entry_price_basis": "current_open",
            "stop_out_action": "close_and_halt",
            "prime_first_trading_bar": True,
            "floating_pnl_basis": "bid_ask",
        },
        stop_out_level=99.95,
        trading_start=_TRADING_START,
    )
    return controller._interactor.execute(request)


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
    }


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

    def test_trade_count_close_to_mt5_within_realistic_tolerance(self, reconcile):
        # 全修正 ON（層1+層2）+ warmup で実走したトレード総数 = 1164。MT5 = 1163。
        # 差 ≈ 1 は sub-minute 時刻表現の残差（stop-out は層2 で MT5 と同じ 13:07 に一致）。
        ours = len(reconcile["ours"])
        assert ours == 1164  # 実測固定（warmup + 層1 + 層2）
        assert abs(ours - _MT5_TRADES) <= 25  # MT5 との乖離トレランス（退行検出）

    def test_net_profit_close_to_mt5_within_realistic_tolerance(self, reconcile):
        # net profit = -6173.9（MT5 = -6169.0）。差 ≈ 4.9 はトレード差＋stop-out 価格差。
        # 層2（stop-out 時刻一致）優先のトレードオフ（層1単独なら -6168.9 で bit-exact）。
        assert reconcile["net"] == pytest.approx(-6173.9, abs=0.1)  # 実測固定
        assert abs(reconcile["net"] - _MT5_NET) <= 60.0  # MT5 との乖離トレランス

    def test_final_balance_close_to_mt5_within_realistic_tolerance(self, reconcile):
        # 最終 balance = 3826.1（MT5 = 3831.0・初期 10000）。net と整合する現実的残差。
        assert reconcile["balance"] == pytest.approx(3826.1, abs=0.1)  # 実測固定
        assert reconcile["balance"] == pytest.approx(
            _INITIAL_DEPOSIT + reconcile["net"], abs=0.1
        )  # balance = 初期証拠金 + net（自己整合）
        assert abs(reconcile["balance"] - _MT5_BALANCE) <= 60.0  # MT5 との乖離トレランス

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

    def test_stop_out_fires_on_sell_at_mt5_bar_13_07(self, reconcile):
        # 層2（bid_ask 含み損評価）の回帰固定: stop-out 強制決済は SELL 側で MT5 と同じ
        # 2025-01-13T13:07 のバーで発火する（同バー建て→同バー強制決済で entry=exit=13:07）。
        # 層2 を無効化すると stop-out が 13:16（層1単独）へずれ本アサートが落ちる。
        stop_outs = [t for t in reconcile["ours"] if t.exit_reason == "stop_out"]
        assert len(stop_outs) == 1, f"stop-out 強制決済は 1 件（実測 {len(stop_outs)}）"
        so = stop_outs[0]
        assert so.side == "sell", f"stop-out は SELL 側で発火（実測 {so.side}・層2 退行）"
        assert so.entry_time == _OUR_STOPOUT_ENTRY  # 2025-01-13T13:07（MT5 停止バーに一致）
        assert so.exit_time == np.datetime64("2025-01-13T13:07:00")

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
