"""E-BacktestTestResult: 検証採否（Value Object・詳細設計 §3.5）。

検証手続き S1〜S6（仕様 §3.2）の最終結果。verdict は 4 値（採用/戦略棄却/不採用/
サンプル下限未達）。各検定 p 値・選択候補・OOS 指標を記録する（DoD・棄却時も最良 f_k）。

domain 層は外部依存ゼロ（標準ライブラリのみ）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    ADOPT = "adopt"
    REJECT_STRATEGY = "reject_strategy"
    NOT_ADOPT = "not_adopt"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


@dataclass(frozen=True)
class BacktestTestResult:
    verdict: Verdict
    spa_p: float | None
    selected_e: str | None
    selected_p_tp: float | None
    best_f_k: float | None
    kupiec_p: float | None
    christoffersen_p: float | None
    tp_calibration_diff: float | None
    oos_mean_weekly_net_return: float | None
    oos_weeks: int
    oos_stop_hits: int
