"""TDD 単体: walk_forward UC 純関数（schedule_windows/stitch_oos/aggregate_efficiency）と
依存方向 ast 検査（詳細設計 §8.1 U-1..U-17/U-23/U-24・条件2 機械検証ガード U-13/U-23）。

engine 不要（domain 依存のみ・純関数は全分岐到達可能）。時刻型は numpy.datetime64 と
int(epoch 秒) の両方で決定論を検証する。
"""
from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import numpy as np
import pytest

from simulator.usecase.models import BacktestStats
from simulator.usecase.run_is_oos import DegradationReport, MetricDegradation
from simulator.usecase.walk_forward import (
    StitchedOosSummary,
    WalkForwardError,
    WfEfficiency,
    WindowResult,
    WindowSpec,
    aggregate_efficiency,
    schedule_windows,
    stitch_oos,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_D = np.datetime64  # 短縮
_DAY = np.timedelta64(1, "D")


# --- 共通ヘルパ -------------------------------------------------------------

def _make_stats(**overrides) -> BacktestStats:
    """全 40 フィールドを 0.0 既定で構築し overrides で上書きした BacktestStats。"""
    base = {f.name: (0 if f.type == "int" else 0.0) for f in fields(BacktestStats)}
    base.update(overrides)
    return BacktestStats(**base)


def _make_window_result(*, index: int, profit_ratio) -> WindowResult:
    """profit の degradation.ratio が指定値の WindowResult（efficiency テスト用）。"""
    md = MetricDegradation(
        name="profit", is_value=100.0, oos_value=50.0, ratio=profit_ratio, delta=-50.0
    )
    deg = DegradationReport(metrics=[md])
    spec = WindowSpec(index=index, is_start=_D("2026-01-01"), split=_D("2026-02-01"),
                      oos_end=_D("2026-03-01"))
    return WindowResult(
        window=spec, best_params={}, is_stats=_make_stats(),
        oos_stats=_make_stats(), degradation=deg, optimize_result=None,
    )


# --- U-1: rolling 窓境界 -----------------------------------------------------

def test_schedule_rolling_window_boundaries():
    # Arrange: 全期間 100D、is=30D、oos=10D、step=10D
    gs, ge = _D("2026-01-01"), _D("2026-01-01") + 100 * _DAY
    # Act
    windows = schedule_windows(mode="rolling", global_start=gs, global_end=ge,
                               is_span=30 * _DAY, oos_span=10 * _DAY, step=10 * _DAY)
    # Assert: rolling 式 is_start=gs+i*step / split=is_start+is_span / oos_end=split+oos_span
    w0 = windows[0]
    assert w0.index == 0
    assert w0.is_start == gs
    assert w0.split == gs + 30 * _DAY
    assert w0.oos_end == gs + 40 * _DAY
    w1 = windows[1]
    assert w1.is_start == gs + 10 * _DAY
    assert w1.split == gs + 40 * _DAY
    assert w1.oos_end == gs + 50 * _DAY


# --- U-2: anchored 窓境界 ----------------------------------------------------

def test_schedule_anchored_window_boundaries():
    gs, ge = _D("2026-01-01"), _D("2026-01-01") + 100 * _DAY
    windows = schedule_windows(mode="anchored", global_start=gs, global_end=ge,
                               is_span=30 * _DAY, oos_span=10 * _DAY, step=10 * _DAY)
    # is_start 全窓不変・split 拡張
    assert all(w.is_start == gs for w in windows)
    assert windows[0].split == gs + 30 * _DAY
    assert windows[1].split == gs + 40 * _DAY
    assert windows[0].oos_end == gs + 40 * _DAY
    assert windows[1].oos_end == gs + 50 * _DAY


# --- U-3: oos_end == global_end ちょうどは採用（<=） -------------------------

def test_schedule_boundary_inclusive_oos_end_equals_global_end():
    gs = _D("2026-01-01")
    # is=30D oos=10D → 単一窓 oos_end=gs+40D。global_end=gs+40D ちょうど。
    ge = gs + 40 * _DAY
    windows = schedule_windows(mode="rolling", global_start=gs, global_end=ge,
                               is_span=30 * _DAY, oos_span=10 * _DAY, step=10 * _DAY)
    assert len(windows) == 1
    assert windows[0].oos_end == ge  # ちょうど一致でも採用（H-3 <=）


# --- U-4: 端数 OOS 窓の切り捨て（oos_end > global_end） ---------------------

def test_schedule_truncates_partial_oos():
    gs = _D("2026-01-01")
    # global_end=gs+45D。窓0 oos_end=gs+40D（採用）/窓1 oos_end=gs+50D>45D（不採用）。
    ge = gs + 45 * _DAY
    windows = schedule_windows(mode="rolling", global_start=gs, global_end=ge,
                               is_span=30 * _DAY, oos_span=10 * _DAY, step=10 * _DAY)
    assert len(windows) == 1  # 端数窓1は不採用


# --- U-5: 終了条件不成立後に後続を生成しない（単調打ち切り） ----------------

def test_schedule_monotone_stop():
    gs = _D("2026-01-01")
    ge = gs + 60 * _DAY
    windows = schedule_windows(mode="rolling", global_start=gs, global_end=ge,
                               is_span=30 * _DAY, oos_span=10 * _DAY, step=10 * _DAY)
    # 窓 index は 0..n-1 で連続・最後の窓の次は生成されない
    assert [w.index for w in windows] == list(range(len(windows)))
    last = windows[-1]
    assert last.oos_end <= ge
    assert last.oos_end + 10 * _DAY > ge  # 次窓があれば oos_end 超過


# --- U-6: 窓 0 件で WalkForwardError -----------------------------------------

def test_schedule_empty_raises():
    gs = _D("2026-01-01")
    ge = gs + 20 * _DAY  # is_span(30)+oos_span(10)=40 > 20 → 窓 0 件
    with pytest.raises(WalkForwardError) as ei:
        schedule_windows(mode="rolling", global_start=gs, global_end=ge,
                         is_span=30 * _DAY, oos_span=10 * _DAY, step=10 * _DAY)
    # context に span/step を載せる（無音禁止）
    assert "is_span" in ei.value.context
    assert "oos_span" in ei.value.context


# --- U-7: 不正 mode で WalkForwardError --------------------------------------

def test_schedule_invalid_mode_raises():
    gs = _D("2026-01-01")
    ge = gs + 100 * _DAY
    with pytest.raises(WalkForwardError):
        schedule_windows(mode="diagonal", global_start=gs, global_end=ge,
                         is_span=30 * _DAY, oos_span=10 * _DAY, step=10 * _DAY)


# --- U-8: int epoch 時刻型でも同一ロジックで決定論 --------------------------

def test_schedule_int_time_type():
    # int epoch 秒。is=30 oos=10 step=10 global=[0,100)
    windows = schedule_windows(mode="rolling", global_start=0, global_end=100,
                               is_span=30, oos_span=10, step=10)
    assert windows[0].is_start == 0
    assert windows[0].split == 30
    assert windows[0].oos_end == 40
    assert windows[1].is_start == 10
    # datetime64 と同一の窓数（境界 100 ちょうども <= で採用）
    assert windows[-1].oos_end <= 100


# --- U-9: stitch 加法総和（区分 A） -----------------------------------------

def test_stitch_additive_sum():
    s1 = _make_stats(profit=100.0, gross_profit=150.0, gross_loss=-50.0, trades=10,
                     profit_trades=6, loss_trades=4)
    s2 = _make_stats(profit=200.0, gross_profit=260.0, gross_loss=-60.0, trades=20,
                     profit_trades=12, loss_trades=8)
    out = stitch_oos([s1, s2])
    assert out.additive["profit"] == 300.0
    assert out.additive["gross_profit"] == 410.0
    assert out.additive["gross_loss"] == -110.0
    assert out.additive["trades"] == 30.0
    assert out.window_count == 2


# --- U-10: stitch 母数再計算（区分 B：A 総和から、窓別平均でない） -----------

def test_stitch_recomputed_from_totals():
    s1 = _make_stats(profit=100.0, gross_profit=150.0, gross_loss=-50.0, trades=10,
                     profit_trades=5, loss_trades=5)
    s2 = _make_stats(profit=200.0, gross_profit=250.0, gross_loss=-50.0, trades=10,
                     profit_trades=5, loss_trades=5)
    out = stitch_oos([s1, s2])
    # profit_factor = Σgp/Σgl = 400 / -100 = -4.0（窓別比率の平均ではない）
    assert out.recomputed["profit_factor"] == 400.0 / -100.0
    # expected_payoff = Σprofit/Σtrades = 300/20
    assert out.recomputed["expected_payoff"] == 300.0 / 20.0
    # average_profit_trade = Σgp/Σprofit_trades = 400/10
    assert out.recomputed["average_profit_trade"] == 400.0 / 10.0
    # average_loss_trade = Σgl/Σloss_trades = -100/10
    assert out.recomputed["average_loss_trade"] == -100.0 / 10.0


# --- U-11: stitch 母数 0 で None（ゼロ除算回避） ----------------------------

def test_stitch_recomputed_zero_denominator_none():
    s = _make_stats(profit=0.0, gross_profit=0.0, gross_loss=0.0, trades=0,
                    profit_trades=0, loss_trades=0)
    out = stitch_oos([s])
    assert out.recomputed["profit_factor"] is None       # Σgross_loss==0
    assert out.recomputed["expected_payoff"] is None      # Σtrades==0
    assert out.recomputed["average_profit_trade"] is None  # Σprofit_trades==0
    assert out.recomputed["average_loss_trade"] is None    # Σloss_trades==0


# --- U-12: 区分 C は窓別系列のみ・通期スカラ非出力 --------------------------

def test_stitch_non_stitchable_per_window_only():
    s1 = _make_stats(sharpe_ratio=0.8, equity_dd_max=10.0, recovery_factor=2.0)
    s2 = _make_stats(sharpe_ratio=1.1, equity_dd_max=20.0, recovery_factor=3.0)
    out = stitch_oos([s1, s2])
    # 区分 C キーは additive / recomputed に存在しない
    assert "sharpe_ratio" not in out.additive
    assert "sharpe_ratio" not in out.recomputed
    assert "equity_dd_max" not in out.additive
    assert "recovery_factor" not in out.recomputed
    # 窓別系列としてのみ提示
    assert out.per_window["sharpe_ratio"] == [0.8, 1.1]
    assert out.per_window["equity_dd_max"] == [10.0, 20.0]


# --- U-13: 3 分類が BacktestStats 全フィールドを網羅かつ互いに素 ------------

def test_stitch_classification_covers_all_fields():
    from simulator.usecase import walk_forward as wf

    additive = set(wf._ADDITIVE_FIELDS)
    nonstitch = set(wf._NON_STITCHABLE_FIELDS)
    # recomputed のキー集合は stitch 結果から取得（区分 B）
    out = stitch_oos([_make_stats()])
    recomputed = set(out.recomputed.keys())

    all_fields = {f.name for f in fields(BacktestStats)}
    # 網羅: 3 集合の和が全フィールドと一致
    assert additive | recomputed | nonstitch == all_fields
    # 互いに素
    assert additive & recomputed == set()
    assert additive & nonstitch == set()
    assert recomputed & nonstitch == set()
    # 件数（10+4+26=40）
    assert len(additive) == 10
    assert len(recomputed) == 4
    assert len(nonstitch) == 26


# --- U-14: 空列で WalkForwardError -------------------------------------------

def test_stitch_empty_raises():
    with pytest.raises(WalkForwardError):
        stitch_oos([])


# --- U-15: efficiency 中央値・最小値（finite のみ） -------------------------

def test_efficiency_profit_ratio_median_min():
    wrs = [
        _make_window_result(index=0, profit_ratio=0.4),
        _make_window_result(index=1, profit_ratio=0.6),
        _make_window_result(index=2, profit_ratio=0.8),
    ]
    eff = aggregate_efficiency(wrs, metric="profit")
    assert eff.metric == "profit"
    assert eff.per_window_ratio == [0.4, 0.6, 0.8]
    assert eff.finite_ratios == [0.4, 0.6, 0.8]
    assert eff.excluded_none_count == 0
    assert eff.median == 0.6
    assert eff.minimum == 0.4


# --- U-16: ratio None 窓を除外＋件数 ----------------------------------------

def test_efficiency_excludes_none_windows():
    wrs = [
        _make_window_result(index=0, profit_ratio=0.4),
        _make_window_result(index=1, profit_ratio=None),  # IS profit=0 窓
        _make_window_result(index=2, profit_ratio=0.8),
    ]
    eff = aggregate_efficiency(wrs, metric="profit")
    assert eff.per_window_ratio == [0.4, None, 0.8]
    assert eff.finite_ratios == [0.4, 0.8]
    assert eff.excluded_none_count == 1
    assert eff.median == pytest.approx(0.6)  # finite のみ (0.4+0.8)/2
    assert eff.minimum == 0.4


# --- U-17: 全窓 None で median/minimum=None・件数=W -------------------------

def test_efficiency_all_none_returns_none():
    wrs = [
        _make_window_result(index=0, profit_ratio=None),
        _make_window_result(index=1, profit_ratio=None),
    ]
    eff = aggregate_efficiency(wrs, metric="profit")
    assert eff.finite_ratios == []
    assert eff.excluded_none_count == 2
    assert eff.median is None
    assert eff.minimum is None


# --- U-23: walk_forward.py の禁止 import 不在（ast 検査・依存方向 C-W4） -----

def test_walk_forward_py_import_dependency():
    path = _REPO_ROOT / "simulator" / "usecase" / "walk_forward.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
    forbidden = {"simulator.main", "simulator.adapter", "simulator.tools"}
    for mod in mods:
        top = mod.split(".")[0]
        assert top not in ("pandas", "numpy"), f"walk_forward imports {mod}"
        for f in forbidden:
            assert not (mod == f or mod.startswith(f + ".")), f"forbidden import {mod}"


# --- U-24: 窓内 OptimizeError → WalkForwardError（context に window_index） --

def test_walk_forward_oos_optimize_error_raises_with_window_id():
    from simulator.usecase.optimize import OptimizeError
    from simulator.usecase.walk_forward import WalkForwardRequest, walk_forward

    gs = _D("2026-01-01")
    ge = gs + 40 * _DAY  # 単一窓
    req = WalkForwardRequest(
        mode="rolling", global_start=gs, global_end=ge,
        is_span=30 * _DAY, oos_span=10 * _DAY, step=10 * _DAY,
        search_space={"lot_size": [0.1]}, max_total_runs=100,
    )

    class _StubSearch:
        def theoretical_count(self, search_space):
            return 1

        def candidates(self, search_space):
            return iter([{"lot_size": 0.1}])

    class _StubObjective:
        name = "net"

        def score(self, stats):
            return 0.0

    class _Bar:
        def __init__(self, t):
            self.time = t

    def _provider(is_start, oos_end):
        # 全バーが split 以上 → optimize の slice_is_bars が IS 空 → OptimizeError。
        # split = is_start + is_span = gs + 30D。oos_end 直前のバーのみ返す。
        return [_Bar(oos_end - 1 * _DAY)]

    def _make_run_segment(params):
        def _rs(bars, trading_start):
            raise AssertionError("should not be called")
        return _rs

    # optimize が IS 空（bar.time < split を満たすバー 0 件）で OptimizeError を出す。
    # WF はそれを window_index 付きで昇格する。
    with pytest.raises(WalkForwardError) as ei:
        walk_forward(
            request=req, window_bars_provider=_provider,
            make_run_segment=_make_run_segment,
            search_port=_StubSearch(), objective_port=_StubObjective(),
        )
    assert ei.value.context.get("window_index") == 0
