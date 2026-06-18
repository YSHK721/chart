"""実 MT5 突合 cycle4: MA_Slope_EA + JP225 を Composition Root で実走し MT5実測と突合する。

load_case → build_interactor → CSV 実走 → BacktestResult を expected.deals/results と
比較し、一致率を定量化する。完全一致を捏造せず、残差（warmup 局所 / sub-minute 時刻表現 /
stop-out 発火バー精度）を不変条件テストとして固定する。

実走 config（cycle4 両修正 ON・本テストが固定する条件）:
  entry_price_basis="current_open" / spread は Bar から取得 /
  stop_out_action="close_and_halt" / stop_out_level=99.95。
  これにより MT5 と同じく「証拠金枯渇で全玉強制決済し以降停止」する挙動で実走する
  （cycle3 の stop_out 無効化＝全期間走り切りは廃止。両修正 ON 単一 config に統一）。

実測サマリ（本テストが固定する観測値・2026-06 実走・上記 config）:
  - 我々の往復トレード = 1143、MT5 = 1163。差 ≈ 20 は warmup（初回・Dec2024 履歴欠）+
    sub-minute 時刻表現 + stop-out 発火バー精度に起因（MT5 は 2025-01-13T13:07 まで継続、
    我々は同日 10:03 で強制決済停止＝発火バーが数十分早い）。
  - net profit = -6148.9（MT5 = -6169.0）/ 最終 balance = 3851.1（MT5 = 3831.0・
    初期証拠金 10000）。差 ≈ 20 は上記トレード差＋stop-out 価格バー差由来で現実的範囲。
  - side + entry_time 一致率 = 96.4%（1121/1163）→ 戦略ロジック・エントリ時刻はほぼ一致。
    cycle3（stop_out 無効・全期間）の 98.1% より低いのは退行ではなく、stop-out 有効化で
    我々が 10:03 停止し以降のエントリ（MT5 は 13:07 まで継続）を生成しない必然の差。
  - **SELL トレードは entry/exit 価格とも完全一致（564/564）**。cycle4 バグ① 修正
    （reverse 決済 = 買い戻し = ask(open+spread×point)）により spread が正しく加算される。
    修正前は engine=bid(open) で spread 未加算のため exit 0 件一致だった。
  - **BUY トレードは entry 価格 100% 一致（557/557）・exit 価格は 556/557 一致**。
    唯一の不一致は最終 stop-out 強制決済バー（entry 2025-01-01-13T10:00）。我々の停止
    バー（10:03）と MT5 の停止バー（13:07）が異なるため、その 1 件のみ exit 価格・時刻が
    ずれる（stop-out 発火バー精度の残差）。通常決済（reverse）の BUY exit は全件一致。
  - 初回 BUY の fill 価格式 open+spread×point は再現する（warmup により時刻は 01:01→01:02
    と 1 バーずれる。MT5 は 2024 履歴で EMA 収束済・CSV は 2025-01-02 01:00 始点でシード差）。

本テストは上記を「現実的トレランスの不変条件」で固定し、退行（戦略ロジック破壊・
エントリ時刻一致率低下・SELL exit spread 未加算への退行・BUY 価格不一致・trades/net/
balance の MT5 乖離拡大）を検出する。報告値とテスト固定値は本テスト内で自己完結する
（どの config でどの数値が出るかをテストが実走して確定する）。
"""
from __future__ import annotations

import numpy as np
import pytest

from backtest.main import build_interactor
from backtest.tests.fixtures.mt5 import load_case

_CASE = "ma_slope_jp225_202501"
# 我々の stop-out 強制決済バー（close_and_halt が発火した最終バー）。MT5 の停止バー
# （13:07）とはバー精度が異なり、この 1 件のみ BUY exit が乖離する。
_OUR_STOPOUT_ENTRY = np.datetime64("2025-01-13T10:00:00")
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
        data_path=case.input_csv,
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
        # cycle4 両修正 ON: current_open + spread from bar に加え、stop_out を MT5 同様
        # 「強制決済して停止」（close_and_halt）化し stop_out_level=99.95 で発火させる。
        config_overrides={
            "tick_model": "open_only",
            "entry_price_basis": "current_open",
            "stop_out_action": "close_and_halt",
        },
        stop_out_level=99.95,
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
        # 両修正 ON（close_and_halt + stop_out_level=99.95）で実走したトレード総数 = 1143。
        # MT5 = 1163。差 ≈ 20 は warmup（初回・Dec2024 履歴欠）+ sub-minute 時刻表現 +
        # stop-out 発火バー精度（我々 10:03 停止 / MT5 13:07 継続）に起因する現実的残差。
        ours = len(reconcile["ours"])
        assert ours == 1143  # 実測固定（前回報告 1143 と一致）
        assert abs(ours - _MT5_TRADES) <= 25  # MT5 との乖離トレランス（退行検出）

    def test_net_profit_close_to_mt5_within_realistic_tolerance(self, reconcile):
        # net profit = -6148.9（MT5 = -6169.0）。差 ≈ 20 はトレード差＋stop-out 価格バー差。
        assert reconcile["net"] == pytest.approx(-6148.9, abs=0.1)  # 実測固定
        assert abs(reconcile["net"] - _MT5_NET) <= 60.0  # MT5 との乖離トレランス

    def test_final_balance_close_to_mt5_within_realistic_tolerance(self, reconcile):
        # 最終 balance = 3851.1（MT5 = 3831.0・初期 10000）。net と整合する現実的残差。
        assert reconcile["balance"] == pytest.approx(3851.1, abs=0.1)  # 実測固定
        assert reconcile["balance"] == pytest.approx(
            _INITIAL_DEPOSIT + reconcile["net"], abs=0.1
        )  # balance = 初期証拠金 + net（自己整合）
        assert abs(reconcile["balance"] - _MT5_BALANCE) <= 60.0  # MT5 との乖離トレランス

    def test_first_buy_fill_reproduces_open_plus_spread_times_point(self, reconcile):
        # warmup により初回 BUY 時刻は MT5 01:01 → 我々 01:02 に 1 バーずれる（EMA seed 差）。
        # しかし fill 価格式 open+spread×point は再現する: bar 01:02 open=39452, spread=150,
        # point=0.1 → 39452+150×0.1 = 39467。
        first_buy = next(t for t in reconcile["ours"] if t.side == "buy")
        assert first_buy.entry_price == pytest.approx(39467.0)

    def test_side_and_entry_time_match_rate_at_least_96pct(self, reconcile):
        # 戦略ロジック・エントリ時刻の一致（warmup 局所ずれ・stop-out 停止差を除く主指標）。
        # 両修正 ON での観測 = 96.4%（1121/1163）。cycle3（stop_out 無効・全期間）の 98.1%
        # より低いのは退行ではなく、我々が 10:03 で停止し以降のエントリを生成しない必然差。
        mt5_keys = set(reconcile["mt5_by_key"])
        matched = sum(
            1 for t in reconcile["ours"] if (t.side, t.entry_time) in mt5_keys
        )
        rate = matched / len(reconcile["mt5"])
        assert rate >= 0.96, f"side+entry_time 一致率 {rate:.1%} < 96%（戦略退行の疑い）"

    def test_buy_trades_match_prices_except_stopout_bar(self, reconcile):
        # BUY は entry=ask(=open+spread×pt)・exit=bid(=open) とも MT5 と一致する。
        # 唯一の例外は stop-out 強制決済バー（entry 2025-01-13T10:00）。我々の停止バー
        # （10:03）と MT5 の停止バー（13:07）が異なるため、その 1 件のみ exit 価格がずれる。
        mt5_by_key = reconcile["mt5_by_key"]
        n = entry_ok = exit_ok = 0
        stopout_exit_mismatch = 0
        for t in reconcile["ours"]:
            m = mt5_by_key.get((t.side, t.entry_time))
            if m is None or t.side != "buy":
                continue
            n += 1
            entry_ok += t.entry_price == pytest.approx(m["entry_price"])
            exit_match = t.exit_price == pytest.approx(m["exit_price"])
            exit_ok += exit_match
            if not exit_match and t.entry_time == _OUR_STOPOUT_ENTRY:
                stopout_exit_mismatch += 1
        assert n == 557  # 実測固定（BUY 往復で MT5 とキー一致する件数）
        # BUY entry 価格は全件一致（spread 加算が entry 側で正しい）。
        assert entry_ok == n, f"BUY entry 一致 {entry_ok}/{n}（BUY fill 退行の疑い）"
        # BUY exit は stop-out 強制決済バー 1 件のみ不一致・残り全件一致（556/557）。
        assert exit_ok == n - 1, f"BUY exit 一致 {exit_ok}/{n}（期待 556）"
        # その唯一の不一致が stop-out バーであることを固定（別バーでの退行を排除）。
        assert stopout_exit_mismatch == 1, (
            "BUY exit 不一致が stop-out 強制決済バー(10:00)以外で発生（退行の疑い）"
        )

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
        assert n == 564  # 実測固定（SELL 往復で MT5 とキー一致する件数）
        # SELL entry は一致（売り=bid=open で MT5 と同じ）。
        assert entry_ok == n, f"SELL entry 一致 {entry_ok}/{n}"
        # SELL exit も全件一致する（reverse 決済 ask = open+spread×pt）。
        assert exit_ok == n, (
            f"SELL exit 一致 {exit_ok}/{n}: spread 未加算への退行の疑い "
            "（reverse 決済 ask に spread が加算されていない）"
        )
