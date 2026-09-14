"""TDD 単体: walk_forward_cli 入口検証（詳細設計 §8.1 U-18..U-22）。

🟡-2（random seed/n_samples 未指定→exit 2）・B-2（未知 search_param→exit 2）・
grid は seed 不要（U-21）・_BUILD_INTERACTOR_KEYWORDS が build_interactor 実シグネチャ
と一致（U-22）。engine 不要（入口検証は factory/optimize 呼出前に中断）。
"""
from __future__ import annotations

import inspect

import pytest

from simulator.tools import walk_forward_cli


def _base_argv(extra: list[str]) -> list[str]:
    """入口検証へ到達する最小 argv（factory 呼出前で中断するため実データ不要）。"""
    return [
        "--mode", "rolling",
        "--global-start", "2026-01-01",
        "--global-end", "2026-04-01",
        "--is-span", "30D",
        "--oos-span", "10D",
        "--step", "10D",
        "--max-total-runs", "100",
        "--data-path", "/nonexistent.csv",
        "--ea-name", "StopEntryProbe_EA",
        "--out-dir", "out_wf",
        "--max-candidates", "10",
        "--objective", "net",
        *extra,
    ]


# --- U-18: random で --seed 未指定 → exit 2 ---------------------------------

def test_cli_random_without_seed_exits_2():
    argv = _base_argv([
        "--search-algo", "random", "--n-samples", "5",
        "--search-param", "lot_size=0.1,0.2",
    ])
    with pytest.raises(SystemExit) as ei:
        walk_forward_cli.main(argv)
    assert ei.value.code == 2


# --- U-19: random で --n-samples 未指定 → exit 2 ----------------------------

def test_cli_random_without_n_samples_exits_2():
    argv = _base_argv([
        "--search-algo", "random", "--seed", "42",
        "--search-param", "lot_size=0.1,0.2",
    ])
    with pytest.raises(SystemExit) as ei:
        walk_forward_cli.main(argv)
    assert ei.value.code == 2


# --- U-20: 未知 search_param キー → exit 2（B-2） --------------------------

def test_cli_unknown_search_param_exits_2():
    argv = _base_argv([
        "--search-algo", "grid",
        "--search-param", "bogus_unknown_key=1,2",
    ])
    with pytest.raises(SystemExit) as ei:
        walk_forward_cli.main(argv)
    assert ei.value.code == 2


# --- U-21: grid は seed 不要（入口検証通過） --------------------------------

def test_cli_grid_no_seed_ok():
    # grid + 既知キーは入口検証を通過する。factory 構築（存在しない CSV）で
    # 入口検証より後の段階で別例外になる＝SystemExit(2) ではないことを確認。
    argv = _base_argv([
        "--search-algo", "grid",
        "--search-param", "lot_size=0.1,0.2",
    ])
    with pytest.raises(Exception) as ei:
        walk_forward_cli.main(argv)
    # 入口検証 (parser.error=SystemExit(2)) には該当しない
    if isinstance(ei.value, SystemExit):
        assert ei.value.code != 2


# --- U-22: _BUILD_INTERACTOR_KEYWORDS が build_interactor 実シグネチャと一致 -

def test_keyword_whitelist_matches_build_interactor():
    from simulator.main import build_interactor

    sig_keys = set(inspect.signature(build_interactor).parameters.keys())
    assert walk_forward_cli._BUILD_INTERACTOR_KEYWORDS == sig_keys
