"""cache_layout.current_layouts の公開契約テスト（ISSUE-094 🔵 item4）。

GC ツール（tools/cache_gc.py）が依存する MP 公開記述子の形（キー・現行世代の定数連動）を固定する。
"""

from __future__ import annotations

from pathlib import Path

from market_profile_api import cache_layout
from market_profile_api.compute import market_profile_dwell as mpd
from market_profile_api.compute import market_profile_zp as zp
from market_profile_api.controller import tf_period_profile_controller as tfp


def test_current_layouts_returns_three_systems_with_contract_keys():
    layouts = cache_layout.current_layouts()
    names = {lay["name"] for lay in layouts}
    assert names == {"dwell", "zp-znull", "tf-period"}
    for lay in layouts:
        assert set(lay.keys()) == {"name", "root", "gen_depth", "current", "reason"}
        assert lay["root"] is None or isinstance(lay["root"], Path)
        assert isinstance(lay["gen_depth"], int) and lay["gen_depth"] >= 1
        assert isinstance(lay["current"], frozenset) and lay["current"]
        assert isinstance(lay["reason"], str) and lay["reason"]


def test_current_generation_names_track_code_constants():
    by_name = {lay["name"]: lay for lay in cache_layout.current_layouts()}
    assert by_name["dwell"]["current"] == frozenset({f"g{mpd.GRID_W:g}"})
    assert by_name["zp-znull"]["current"] == frozenset({f"b{zp.ZP_BP:g}"})
    # tf-period: count 世代は _TFP_CACHE_VERSION 連動・zp 世代は s3 固定。
    assert by_name["tf-period"]["current"] == frozenset({f"s{tfp._TFP_CACHE_VERSION}", "s3"})


def test_gen_depths_match_disk_layout():
    by_name = {lay["name"]: lay for lay in cache_layout.current_layouts()}
    assert by_name["dwell"]["gen_depth"] == 2       # <sym>/<gen>
    assert by_name["zp-znull"]["gen_depth"] == 2    # <sym>/<gen>
    assert by_name["tf-period"]["gen_depth"] == 3   # <sym>/<tf>/<gen>
