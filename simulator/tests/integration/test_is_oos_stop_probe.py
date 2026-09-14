"""TDD 結合: 先例 2026-04 StopEntryProbe 再現（詳細設計 §6.3 / 条件1,2,5）。

read-only fixture bars_m1.csv（full）を 1 本だけ使い、本 UC 経由（slice_is_bars +
make_run_segment）で IS（net+11370/trades 5224）と OOS（net-4020/trades 2438）を再現する。
これが CSV byte-identical（IS=full の head-prefix）と時刻型整合（bar.time<split が真に
IS を切る）の間接実証になる。さらに NFR-S1（既存データ mtime 不変）を assert する。

fixture 不在環境では skip（実 fixture 依存・約 2.3MB CSV）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.main import build_interactor
from simulator.tools.run_is_oos_cli import make_run_segment, normalize_time
from simulator.usecase.run_is_oos import RunIsOosRequest, run_is_oos

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


def _stop_probe_kwargs(csv_path: Path) -> dict:
    # reconcile.py:112-124 と同一 config（保証境界 C-1: pending_lifecycle 経路）
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


def test_is_oos_stop_probe_reproduces_precedent_and_no_data_mutation(tmp_path):
    # Arrange: 既存データ mtime スナップショット（NFR-S1）
    before = _snapshot_mtimes(_DATA_DIRS)

    controller, request = build_interactor(**_stop_probe_kwargs(_FIXTURE))
    sample_time = request.bars[0].time
    run_segment = make_run_segment(controller, request)

    # Act: 単一 CSV ＋ in-memory head 切りで IS/OOS を再現
    result = run_is_oos(
        request=RunIsOosRequest(
            split=normalize_time("2026-04-15", sample_time),
            is_trading_start=normalize_time("2026-04-01", sample_time),
        ),
        full_bars=request.bars,
        run_segment=run_segment,
    )

    # Assert: 先例 bit-exact（reconcile_is.py / reconcile.py の固定値）
    #
    # ⚠ ISSUE-445 段階 B/C: 以下 4 行は**是正で動かない**ピンである（実測 2026-08-26）。
    # 段階 C で `_stop_probe_kwargs` の銘柄仕様を供給元へ**対で**寄せた結果 `volume_min` が
    # 0.01 → 1.0 になり `NormalizeLot` が lot を持ち上げるため、積 `lot × contract_size`
    # は 0.1 × 10.0 = 1.0 × 1.0 で不変になり、この 4 値は 1 ビットも動かなかった（実走で確認）。
    # 下記は**是正前に測った値のまま**である。
    # 一方 `contract_size` だけを寄せると profit +11370 → **+1137**・OOS trades 2438 →
    # **4877** に壊れる。赤になったら**期待値を書き換えず**、是正が片側だけになって
    # いないかを疑うこと。
    assert result.is_stats.trades == 5224
    assert result.is_stats.profit == 11370.0
    assert result.oos_stats.trades == 2438
    assert result.oos_stats.profit == -4020.0

    # Assert: 劣化レポートも算出される
    prof = result.degradation.by_name("profit")
    assert prof.delta == -4020.0 - 11370.0

    # Assert: NFR-S1 既存データ非波及（mtime 不変）
    after = _snapshot_mtimes(_DATA_DIRS)
    assert before == after
