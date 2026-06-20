"""TDD 回帰: WF 出力 meta 必須キー集合の直接 assert（詳細設計 §5.2 / §5.4・🟡-2）。

🟡-2: 出力 walk_forward.json の meta が設計§5.2 列挙の必須キー集合
      （mode/global_start/global_end/is_span/oos_span/step/objective/search_algo/
       window_count/total_run_estimate/max_total_runs/efficiency_excluded_none）を
      すべて含むことを「絶対的キー存在検証」で assert（相互 byte 比較でなく）。
      report.md ヘッダにも `- objective: {..}  search_algo: {..}` 行が出ることを assert。

fixture 依存（read-only・grid 決定論）。fixture 不在環境では skip。
"""
from __future__ import annotations

from pathlib import Path

import json

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2].parent
_FIXTURE = (
    _REPO_ROOT / "simulator" / "tests" / "confirmation"
    / "2026-04_stop-probe_oos" / "bars_m1.csv"
)

# 設計§5.2 が列挙する meta 必須キー集合（objective/search_algo を含む）。
_REQUIRED_META_KEYS = frozenset({
    "mode", "global_start", "global_end", "is_span", "oos_span", "step",
    "objective", "search_algo",
    "window_count", "total_run_estimate", "max_total_runs",
    "efficiency_excluded_none",
})


def _run_main(tmp_path: Path, out_name: str):
    """fixture 1 窓 grid を main() で実行し out_dir へ出力（決定論）。"""
    from simulator.tools.walk_forward_cli import main

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
    return tmp_path / out_name


# --- 🟡-2 (JSON): meta 必須キー集合の絶対的存在検証 ------------------------

@pytest.mark.skipif(not _FIXTURE.exists(), reason=f"fixture 不在: {_FIXTURE}")
def test_meta_contains_required_keys_including_objective_and_search_algo(tmp_path):
    out_dir = _run_main(tmp_path, "run_meta")
    meta = json.loads((out_dir / "walk_forward.json").read_text(encoding="utf-8"))["meta"]

    missing = _REQUIRED_META_KEYS - set(meta.keys())
    assert not missing, f"meta に必須キー欠落: {sorted(missing)}"
    # objective/search_algo の値が argv と一致（設計§5.2 表記）
    assert meta["objective"] == "net"
    assert meta["search_algo"] == "grid"


# --- 🟡-2 (MD): ヘッダに objective/search_algo 行が出る ---------------------

@pytest.mark.skipif(not _FIXTURE.exists(), reason=f"fixture 不在: {_FIXTURE}")
def test_markdown_header_contains_objective_and_search_algo(tmp_path):
    out_dir = _run_main(tmp_path, "run_md")
    md = (out_dir / "report.md").read_text(encoding="utf-8")

    assert "- objective: net  search_algo: grid" in md
