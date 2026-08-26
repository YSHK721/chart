"""TDD 結合: 単一候補空間（縮退）で SP1 結合（先例 2026-04）と一致（詳細設計 §6.3・条件5）。

read-only fixture bars_m1.csv（full）を本 optimize 経由（make_run_segment_factory +
optimize）で IS（net+11370/trades 5224）・OOS（net-4020/trades 2438）を再現する。
単一候補空間では best=唯一候補となり、IS/OOS run は SP1 結合と一致する。
さらに NFR-OS1（既存データ mtime 不変）を assert する。

fixture 不在環境では skip（実 fixture 依存・約 2.3MB CSV）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields

_REPO_ROOT = Path(__file__).resolve().parents[2].parent
_FIXTURE = (
    _REPO_ROOT
    / "simulator"
    / "tests"
    / "confirmation"
    / "2026-04_stop-probe_oos"
    / "bars_m1.csv"
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
    # reconcile.py:112-124 / SP1 結合と同一 config（決定論）
    return dict(
        data_path=str(csv_path),
        symbol="JP225",
        period="M1",
        ea_name="StopEntryProbe_EA",
        initial_deposit=10_000.0,
        # 銘柄仕様 8 キーは供給元スナップショットだけを権威とする（ISSUE-445 段階 C）。
        # ここにリテラルを書かない＝人が値を選べない。
        **load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225"),
        ma_period=60,
        ma_method="ema",
        lot_size=0.1,
        stop_loss_points=200,
        take_profit_points=500,
        entry_offset_points=100.0,
        entry_type="stop",
        config_overrides={
            "tick_model": "ohlc_expand",
            "entry_price_basis": "current_open",
            "floating_pnl_basis": "bid_ask",
            "stop_out_action": "close_and_halt",
            "session_calendar": "jp225",
            "profit_round_digits": 0,
            "stop_out_at_open": True,
            "pending_lifecycle": True,
            "pending_oco": True,
            "pending_persistent": True,
            "hedged_margin": True,
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


def test_optimize_degenerate_single_candidate_matches_sp1_precedent_and_no_data_mutation():
    from simulator.tools.optimize_cli import make_run_segment_factory
    from simulator.usecase.optimize import OptimizeRequest, optimize
    from simulator.usecase.optimize_strategies import GridSearch, NetProfitObjective

    # Arrange: 既存データ mtime スナップショット（NFR-OS1）
    before = _snapshot_mtimes(_DATA_DIRS)

    factory, full_bars, split, is_start = make_run_segment_factory(
        _base_kwargs(_FIXTURE),
        split_str="2026-04-15",
        is_trading_start_str="2026-04-01",
    )

    # Act: 単一候補空間（base 既定と同じ stop_loss_points=200 のみ＝縮退）
    result = optimize(
        request=OptimizeRequest(
            search_space={"stop_loss_points": [200]},
            split=split,
            is_trading_start=is_start,
        ),
        full_bars=full_bars,
        make_run_segment=factory,
        search_port=GridSearch(max_candidates=10),
        objective_port=NetProfitObjective(),
    )

    # Assert: best=唯一候補。IS/OOS が先例 bit-exact（reconcile_is.py / reconcile.py）
    #
    # ⚠ ISSUE-445 段階 B/C: 以下の IS/OOS 4 値は**是正で動かない**ピンである
    # （実測 2026-08-26）。段階 C で `_base_kwargs` の銘柄仕様を供給元へ**対で**寄せた結果、
    # 積 `lot × contract_size` が 0.1 × 10.0 = 1.0 × 1.0 で不変になり、この 4 値は
    # 1 ビットも動かなかった（実走で確認・下記は是正前に測った値のまま）。
    # 一方 `contract_size` だけを寄せると
    # profit +11370 → **+1137** に壊れる。赤になったら**期待値を書き換えず**、
    # 是正が片側だけになっていないかを疑うこと。
    assert result.best_params == {"stop_loss_points": 200}
    assert result.best_is_stats.trades == 5224
    assert result.best_is_stats.profit == 11370.0
    assert result.oos_stats.trades == 2438
    assert result.oos_stats.profit == -4020.0
    assert result.total_candidates == 1
    assert result.finite_candidates == 1
    assert result.excluded_count == {"nonfinite": 0, "failed": 0}

    # Assert: 劣化レポートも算出される
    prof = result.degradation.by_name("profit")
    assert prof.delta == -4020.0 - 11370.0

    # Assert: NFR-OS1 既存データ非波及（mtime 不変）
    after = _snapshot_mtimes(_DATA_DIRS)
    assert before == after
