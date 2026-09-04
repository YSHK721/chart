"""CLI: run_scan_contacts_cli（argparse → scan_contacts → JSON 出力 / OutputGuard 拒否）。

df_loader / ma_computer / ticks_factory を注入（合成データ・実ティック不要）。
run_is_oos_cli.assert_safe_output_dir を流用した書込先ガードの拒否も固定する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulator.tools.run_is_oos_cli import OutputGuardError
from simulator.tools.run_scan_contacts_cli import LoadedBars, main


def _fake_df_loader(args):
    return LoadedBars(
        bar_times=[0, 60, 120],
        highs=[110.0, 110.0, 120.0],
        lows=[90.0, 90.0, 100.0],
        closes=[105.0, 108.0, 115.0],
        source_prices=[105.0, 108.0, 115.0],
    )


def _fake_ma(source_prices, length):
    return {0: 100.0, 1: 200.0, 2: 123.0}


def _fake_ticks_factory(args):
    return lambda s, e: [(61, 99.0), (62, 101.0), (63, 98.0)]


def test_cli_writes_events_summary_report(tmp_path: Path):
    argv = ["--ref", "synthetic", "--timeframe", "1m", "--length", "9",
            "--out", "scan_out"]
    rc = main(argv, repo_root=tmp_path, df_loader=_fake_df_loader,
              ma_computer=_fake_ma, ticks_factory=_fake_ticks_factory)
    assert rc == 0

    base = "synthetic_1m_moving_averages_ema9_full_scan"
    out_dir = tmp_path / "scan_out"
    events = json.loads((out_dir / f"{base}.json").read_text(encoding="utf-8"))
    summary = json.loads((out_dir / f"{base}.summary.json").read_text(encoding="utf-8"))

    assert len(events) == 2
    assert [e["direction"] for e in events] == ["up", "down"]
    assert events[0]["schema"] == "contact.v1"
    assert events[0]["tick_time"] == 62
    assert summary["contacts"] == 2
    assert summary["candidate_bars"] == 1
    assert (out_dir / "report.md").exists()


def test_cli_preview_mode_basename_and_no_ticks(tmp_path: Path):
    called = {"n": 0}

    def counting_ticks_factory(args):
        def _fn(s, e):
            called["n"] += 1
            return [(61, 99.0)]
        return _fn

    argv = ["--ref", "synthetic", "--timeframe", "1m", "--length", "9",
            "--no-full-scan", "--out", "scan_out"]
    rc = main(argv, repo_root=tmp_path, df_loader=_fake_df_loader,
              ma_computer=_fake_ma, ticks_factory=counting_ticks_factory)
    assert rc == 0
    base = "synthetic_1m_moving_averages_ema9_preview"
    assert (tmp_path / "scan_out" / f"{base}.json").exists()
    assert called["n"] == 0


@pytest.mark.parametrize("out_dir", ["marketdata/x", "simulator/tests/fixtures/y", "../escape"])
def test_cli_rejects_forbidden_out_dir(tmp_path: Path, out_dir):
    argv = ["--ref", "synthetic", "--timeframe", "1m", "--out", out_dir]
    with pytest.raises(OutputGuardError):
        main(argv, repo_root=tmp_path, df_loader=_fake_df_loader,
             ma_computer=_fake_ma, ticks_factory=_fake_ticks_factory)
