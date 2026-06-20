"""TDD 回帰: WF 決定論（詳細設計 §8.3 R-1/R-2）。

R-1: 同一入力で schedule_windows 2 回が完全一致（engine 不要・純関数）。
R-2: 同一入力で walk_forward_cli.main を 2 回実行し walk_forward.json が byte 同一
     （NFR-WD1・fixture 依存・grid 決定論）。fixture 不在環境では R-2 を skip。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from simulator.usecase.walk_forward import schedule_windows

_REPO_ROOT = Path(__file__).resolve().parents[2].parent
_FIXTURE = (
    _REPO_ROOT / "simulator" / "tests" / "confirmation"
    / "2026-04_stop-probe_oos" / "bars_m1.csv"
)
_D = np.datetime64
_DAY = np.timedelta64(1, "D")


# --- R-1: schedule_windows 決定論（engine 不要） -----------------------------

def test_same_input_same_window_list():
    gs, ge = _D("2026-01-01"), _D("2026-01-01") + 100 * _DAY
    kw = dict(mode="rolling", global_start=gs, global_end=ge,
              is_span=30 * _DAY, oos_span=10 * _DAY, step=10 * _DAY)
    w1 = schedule_windows(**kw)
    w2 = schedule_windows(**kw)
    assert len(w1) == len(w2)
    for a, b in zip(w1, w2):
        assert a.index == b.index
        assert a.is_start == b.is_start
        assert a.split == b.split
        assert a.oos_end == b.oos_end


# --- R-2: walk_forward.json byte 同一（grid 決定論） ------------------------

@pytest.mark.skipif(not _FIXTURE.exists(), reason=f"fixture 不在: {_FIXTURE}")
def test_same_input_byte_identical_json(tmp_path):
    from simulator.tools.walk_forward_cli import main

    def _run(out_name: str) -> bytes:
        argv = [
            "--mode", "rolling",
            "--global-start", "2026-04-01",
            "--global-end", "2026-04-29 23:59",
            "--is-span", "14D",
            "--oos-span", "14D",
            "--step", "14D",
            "--max-total-runs", "100",
            "--data-path", str(_FIXTURE),
            "--ea-name", "StopEntryProbe_EA",
            "--symbol", "JP225", "--period", "M1",
            "--contract-size", "10.0", "--digits", "1", "--point-size", "0.1",
            "--leverage", "10.0", "--lot-size", "0.1",
            "--stop-loss-points", "200", "--take-profit-points", "500",
            "--entry-offset-points", "100.0", "--entry-type", "stop",
            "--config-override", "tick_model=ohlc_expand",
            "--config-override", "entry_price_basis=current_open",
            "--config-override", "floating_pnl_basis=bid_ask",
            "--config-override", "stop_out_action=close_and_halt",
            "--config-override", "session_calendar=jp225",
            "--config-override", "profit_round_digits=0",
            "--config-override", "stop_out_at_open=true",
            "--config-override", "pending_lifecycle=true",
            "--config-override", "pending_oco=true",
            "--config-override", "pending_persistent=true",
            "--config-override", "hedged_margin=true",
            "--search-algo", "grid",
            "--search-param", "stop_loss_points=200,300",
            "--max-candidates", "10",
            "--objective", "net",
            "--out-dir", out_name,
        ]
        rc = main(argv, repo_root=tmp_path)
        assert rc == 0
        return (tmp_path / out_name / "walk_forward.json").read_bytes()

    a = _run("run_a")
    b = _run("run_b")
    assert a == b  # byte 同一（NFR-WD1）
