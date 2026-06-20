"""TDD: bar-mode registry 位置整合（詳細設計 §6.2.4 / C-1）。

bar-mode 経路（MA_Slope_EA・pending_lifecycle 非設定・run_backtest.py:203 enumerate）で、
slice_is_bars 後の request.bars 差し替え（option b・registry 非再構築）が、
IS だけを含む別 build（registry 再構築）と bit-identical な is_stats を生むことを実証する。
causal EMA（main:211-225）により位置 0..k-1 の指標値が一致することの境界テスト。

dataclasses/typing/pathlib 以外に、本テストは tools 層（pandas 許容）の make_run_segment と
usecase の slice_is_bars/RunIsOosRequest/run_is_oos を結線して検証する。
"""
from __future__ import annotations

import math
from dataclasses import asdict
from pathlib import Path

from simulator.main import build_interactor
from simulator.tools.run_is_oos_cli import make_run_segment, normalize_time
from simulator.usecase.run_is_oos import RunIsOosRequest, run_is_oos, slice_is_bars

_HEADER = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"


def _write_mt5_csv(path: Path, n: int) -> Path:
    """振動価格（slope 符号反転で MaSlope を約定させる）。

    単調増加だと約定が 0 件になり stats が trivial に一致してしまい registry 位置整合を
    検証できない（弱 assertion）。振動系列で trades>0 を強制し、約定が ema.iloc[bar_index]
    に依存することで registry 位置ずれを検出可能にする。
    """
    lines = [_HEADER]
    base = 39400.0
    for i in range(n):
        hh = f"{i // 60:02d}"
        mm = f"{i % 60:02d}"
        price = round(base + 50 * math.sin(i / 3.0), 1)
        lines.append(
            f"2025.01.02\t{hh}:{mm}:00\t{price}\t{price + 1}\t{price - 1}\t{price}\t1\t0\t100"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _kwargs(csv_path: Path) -> dict:
    return dict(
        data_path=csv_path,
        symbol="JP225",
        period="M1",
        ea_name="MA_Slope_EA",
        initial_deposit=10_000.0,
        contract_size=10.0,
        volume_min=0.1,
        volume_max=100.0,
        volume_step=0.1,
        stops_level=0,
        digits=1,
        point_size=0.1,
        leverage=10.0,
        ma_period=20,
        ma_method="ema",
        lot_size=0.1,
        stop_loss_points=0,
        take_profit_points=0,
        slope_shift=1,
        slope_min_points=1.0,
        config_overrides={"tick_model": "open_only", "entry_price_basis": "current_open"},
    )


def test_barmode_registry_positions_bit_identical_after_slice(tmp_path):
    """C-1 機構の直接実証: full-build の registry 位置 0..k-1 と is-only-build の
    registry 位置 0..k-1 が causal EMA で bit-identical（registry 非再構築の正当性）。"""
    # Arrange
    full_csv = _write_mt5_csv(tmp_path / "full.csv", n=60)
    controller, _ = build_interactor(**_kwargs(full_csv))
    is_only_csv = _write_mt5_csv(tmp_path / "is_only.csv", n=40)
    controller2, _ = build_interactor(**_kwargs(is_only_csv))
    # Act
    ema_full = controller._interactor._indicators.get("ema")
    ema_is = controller2._interactor._indicators.get("ema")
    # Assert: 位置 0..39 が全て bit-identical（head 切りが位置インデックスを不変に保つ）
    assert all(float(ema_full.iloc[i]) == float(ema_is.iloc[i]) for i in range(40))


def test_barmode_slice_is_bars_matches_separate_truncated_build(tmp_path):
    # Arrange: 全期間 60 本、split を 40 本目の時刻に置く（IS=40 本・OOS=20 本）
    full_csv = _write_mt5_csv(tmp_path / "full.csv", n=60)
    # n=60: index 0..59 → 00:00..00:59。index 40 = 00:40。
    split_str = "2025-01-02T00:40:00"

    controller, request = build_interactor(**_kwargs(full_csv))
    sample_time = request.bars[0].time
    split = normalize_time(split_str, sample_time)

    is_bars = slice_is_bars(list(request.bars), split)
    assert len(is_bars) == 40  # head-prefix 0..39（00:00..00:39 < 00:40）

    # option b: full build の registry をそのまま使い request.bars を IS slice へ差し替え
    run_segment = make_run_segment(controller, request)
    is_stats_optb = run_segment(is_bars, normalize_time("2025-01-02T00:00:00", sample_time))

    # 別 build（registry 再構築）: IS だけを含む CSV を渡して独立に走らせる
    is_only_csv = _write_mt5_csv(tmp_path / "is_only.csv", n=40)
    controller2, request2 = build_interactor(**_kwargs(is_only_csv))
    run_segment2 = make_run_segment(controller2, request2)
    is_stats_rebuilt = run_segment2(
        list(request2.bars), normalize_time("2025-01-02T00:00:00", request2.bars[0].time)
    )

    # Assert: 非 trivial（約定が発生し registry 位置に依存）であることを保証
    assert is_stats_optb.trades > 0
    # Assert: bit-identical（registry 非再構築でも causal EMA で位置 0..k-1 一致）
    assert asdict(is_stats_optb) == asdict(is_stats_rebuilt)


def test_barmode_run_is_oos_is_stats_matches_truncated_build(tmp_path):
    # Arrange: run_is_oos 経由でも option b の IS が独立 IS build と一致
    full_csv = _write_mt5_csv(tmp_path / "full.csv", n=60)
    controller, request = build_interactor(**_kwargs(full_csv))
    sample_time = request.bars[0].time
    run_segment = make_run_segment(controller, request)

    result = run_is_oos(
        request=RunIsOosRequest(
            split=normalize_time("2025-01-02T00:40:00", sample_time),
            is_trading_start=normalize_time("2025-01-02T00:00:00", sample_time),
        ),
        full_bars=request.bars,
        run_segment=run_segment,
    )

    is_only_csv = _write_mt5_csv(tmp_path / "is_only.csv", n=40)
    controller2, request2 = build_interactor(**_kwargs(is_only_csv))
    run_segment2 = make_run_segment(controller2, request2)
    is_stats_rebuilt = run_segment2(
        list(request2.bars), normalize_time("2025-01-02T00:00:00", request2.bars[0].time)
    )

    # Assert: 非 trivial（約定発生）かつ bit-identical
    assert result.is_stats.trades > 0
    assert asdict(result.is_stats) == asdict(is_stats_rebuilt)
