"""TDD 結合: WF→SP2→engine 縮退（単一窓×単一候補）で SP2/SP1 一致（詳細設計 §8.2）。

read-only fixture bars_m1.csv（2026-03-23..2026-04-29）を使い、global_end を window0 の
oos_end ちょうどに置いて単一窓へ縮退させ、WF 経由 oos_stats が SP2 optimize 直呼び
（先例 split=2026-04-15・IS net+11370/5224・OOS -4020/2438）と一致することを検証する。

fixture 不在環境では skip。実行前後の mtime 不変を assert（NFR-WS1・I-5）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields

_REPO_ROOT = Path(__file__).resolve().parents[2].parent
_FIXTURE = (
    _REPO_ROOT / "simulator" / "tests" / "confirmation"
    / "2026-04_stop-probe_oos" / "bars_m1.csv"
)
_DATA_DIRS = [
    _REPO_ROOT / "marketdata",
    _REPO_ROOT / "simulator" / "tests" / "fixtures",
    _REPO_ROOT / "simulator" / "tests" / "confirmation",
]

pytestmark = pytest.mark.skipif(
    not _FIXTURE.exists(),
    reason=f"先例 fixture 不在（read-only・実データ依存）: {_FIXTURE}",
)


def _base_kwargs(csv_path: Path) -> dict:
    # SP2 結合先例（test_optimize_sp1_degenerate.py）と同一 config（決定論）。
    # 銘柄仕様 8 キーは供給元スナップショットだけを権威とする（ISSUE-445 段階 C）。
    return dict(
        data_path=str(csv_path), symbol="JP225", period="M1",
        ea_name="StopEntryProbe_EA", initial_deposit=10_000.0,
        **load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225"),
        ma_period=60, ma_method="ema", lot_size=0.1,
        stop_loss_points=200, take_profit_points=500, entry_offset_points=100.0,
        entry_type="stop",
        config_overrides={
            "tick_model": "ohlc_expand", "entry_price_basis": "current_open",
            "floating_pnl_basis": "bid_ask", "stop_out_action": "close_and_halt",
            "session_calendar": "jp225", "profit_round_digits": 0,
            "stop_out_at_open": True, "pending_lifecycle": True, "pending_oco": True,
            "pending_persistent": True, "hedged_margin": True,
        },
        stop_out_level=100.0,
    )


def _snapshot_mtimes(dirs) -> dict:
    snap = {}
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                snap[str(p)] = p.stat().st_mtime_ns
    return snap


def _setup_single_window(csv_path: Path):
    """単一窓へ縮退する WF 部品を組み立てて返す（split=2026-04-15・先例整合）。

    is_trading_start=2026-04-01・split=2026-04-15。窓 [is_start, oos_end) を
    fixture 全期間に一致させ、is_span=split-is_start、oos_span=oos_end-split。
    """
    import numpy as np

    from simulator.tools.optimize_cli import (
        _build_objective_port, make_run_segment_factory,
    )
    from simulator.tools.run_is_oos_cli import normalize_time
    from simulator.usecase.optimize_strategies import GridSearch

    factory, full_bars, _split_dummy, _is_dummy = make_run_segment_factory(
        _base_kwargs(csv_path), split_str="2026-04-01", is_trading_start_str="2026-04-01"
    )
    sample = full_bars[0].time
    is_start = normalize_time("2026-04-01", sample)
    split = normalize_time("2026-04-15", sample)
    # oos_end = 全 fixture の最後のバー時刻 + 1 分（半開区間右端・全 OOS バー包含）。
    last_time = full_bars[-1].time
    oos_end = last_time + np.timedelta64(1, "m")
    is_span = split - is_start
    oos_span = oos_end - split

    def provider(s, e):
        return [b for b in full_bars if s <= b.time < e]

    return dict(
        factory=factory, full_bars=full_bars, is_start=is_start, split=split,
        oos_end=oos_end, is_span=is_span, oos_span=oos_span, provider=provider,
        search_port=GridSearch(max_candidates=10),
        objective_port=_build_objective_port(type("A", (), {"objective": "net"})()),
    )


# --- I-1/I-2: 単一窓×単一候補が SP2 直呼び（先例）と一致 --------------------

def test_single_window_single_candidate_matches_sp2_and_precedent():
    from simulator.usecase.walk_forward import WalkForwardRequest, walk_forward

    before = _snapshot_mtimes(_DATA_DIRS)
    p = _setup_single_window(_FIXTURE)

    req = WalkForwardRequest(
        mode="rolling", global_start=p["is_start"], global_end=p["oos_end"],
        is_span=p["is_span"], oos_span=p["oos_span"], step=p["oos_span"],
        search_space={"stop_loss_points": [200]}, max_total_runs=100,
    )
    result = walk_forward(
        request=req, window_bars_provider=p["provider"],
        make_run_segment=p["factory"], search_port=p["search_port"],
        objective_port=p["objective_port"],
    )

    # 単一窓
    assert len(result.windows) == 1
    assert len(result.window_results) == 1
    wr = result.window_results[0]
    # 先例 bit-exact（SP2 degenerate と同値）
    #
    # ⚠ ISSUE-445 段階 B/C: 以下の IS/OOS 4 値と stitch 値は**是正で動かない**ピンである
    # （実測 2026-08-26）。段階 C で `_base_kwargs` の銘柄仕様を供給元へ**対で**寄せた結果、
    # 積 `lot × contract_size` が 0.1 × 10.0 = 1.0 × 1.0 で不変になり、1 ビットも動かなかった
    # （実走で確認・下記は是正前に測った値のまま）。
    # 一方 `contract_size` だけを寄せると profit +11370 → **+1137**。
    # 赤になったら**期待値を書き換えず**、是正が片側だけになっていないかを疑うこと。
    assert wr.best_params == {"stop_loss_points": 200}
    assert wr.is_stats.trades == 5224
    assert wr.is_stats.profit == 11370.0
    assert wr.oos_stats.trades == 2438
    assert wr.oos_stats.profit == -4020.0
    # stitch 加法: 単一窓なので profit 総和 = OOS profit
    assert result.stitched_oos.additive["profit"] == -4020.0
    assert result.stitched_oos.window_count == 1

    after = _snapshot_mtimes(_DATA_DIRS)
    assert before == after  # I-5 mtime 不変


# --- I-3: 総 run 回数 = Σ theoretical_count + W（H-1 Port 契約） ------------

def test_total_run_count_matches_port_contract():
    from simulator.usecase.walk_forward import WalkForwardRequest, walk_forward

    p = _setup_single_window(_FIXTURE)
    call_count = {"n": 0}
    factory = p["factory"]

    def counting_factory(params):
        rs = factory(params)

        def wrapped(bars, trading_start):
            call_count["n"] += 1
            return rs(bars, trading_start)
        return wrapped

    # search_space に 3 候補 → theoretical_count=3、W=1 窓 → 総 run = 3 + 1 = 4
    req = WalkForwardRequest(
        mode="rolling", global_start=p["is_start"], global_end=p["oos_end"],
        is_span=p["is_span"], oos_span=p["oos_span"], step=p["oos_span"],
        search_space={"stop_loss_points": [200, 300, 400]}, max_total_runs=100,
    )
    result = walk_forward(
        request=req, window_bars_provider=p["provider"],
        make_run_segment=counting_factory, search_port=p["search_port"],
        objective_port=p["objective_port"],
    )
    tc = p["search_port"].theoretical_count({"stop_loss_points": [200, 300, 400]})
    expected = tc * len(result.windows) + len(result.windows)
    assert call_count["n"] == expected  # = 3 + 1 = 4


# --- I-4: B-1 当窓 full のみで窓内 IS/OOS が正しく分割 ----------------------

def test_b1_window_full_only_correct_split():
    p = _setup_single_window(_FIXTURE)
    # provider が返す当窓 full バー数 = is 区間 + oos 区間
    bars = p["provider"](p["is_start"], p["oos_end"])
    is_bars = [b for b in bars if b.time < p["split"]]
    oos_bars = [b for b in bars if b.time >= p["split"]]
    assert len(is_bars) >= 1
    assert len(oos_bars) >= 1
    assert len(is_bars) + len(oos_bars) == len(bars)  # 半開区間で過不足なし
