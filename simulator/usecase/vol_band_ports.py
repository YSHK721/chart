"""usecase 層の境界（Port）抽象：分散推定・予測ストア（詳細設計 §4.1）。

Protocol を usecase に置く先例（optimize_ports.py）を踏襲。numpy/pandas はここに
漏らさず（実装は adapter 側）、戻り値は domain 型のみ。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:
    from simulator.domain.variance_forecast import VarianceForecast


@runtime_checkable
class VarianceEstimatorPort(Protocol):
    def forecast(
        self,
        rs_plus_series: Sequence[float],
        rs_minus_series: Sequence[float],
        *,
        window: int = 260,
        nw_lag: int = 4,
    ) -> "tuple[float | None, float | None]":
        """log-semivariance-HAR で翌週 (σ̂⁺, σ̂⁻) を予測。算出不可は (None, None)。"""
        ...


@runtime_checkable
class VolBandWriterPort(Protocol):
    """書込ロール（ISP・ISSUE-099 🟡-2）。書込クライアント（estimate_weekly_band）
    は save_all のみ使用するため、書込ロールを 1 メソッド Port として分離する。"""

    def save_all(self, forecasts: "Sequence[VarianceForecast]") -> None: ...


@runtime_checkable
class VolBandReaderPort(Protocol):
    """読取ロール（ISP・ISSUE-099 🟡-2）。読取クライアント（run_weekly_segments）
    は get のみ使用するため、読取ロールを 1 メソッド Port として分離する。"""

    def get(self, week_id: str) -> "VarianceForecast | None": ...
