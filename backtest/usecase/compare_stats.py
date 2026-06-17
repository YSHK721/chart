"""UC-003: BacktestStats(py) と MT5 STAT_* dict を tolerances で照合する純粋比較。

DESIGN §1 許容誤差表に従い、件数（tolerance=0.0）は完全一致、金額は相対誤差で照合する。
ComparisonReport は usecase 層のため pydantic ではなく素の dataclass で定義する
（CLEAN_ARCH §8.3 は pydantic BaseModel を示すが、本層は pydantic 非依存）。

usecase 層は domain のみ依存可。本モジュールは外部 I/O を持たない純粋比較。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ComparisonReport:
    """突合結果。

    matches:    一致した項目 (name, py_value, mt5_value)
    mismatches: 不一致項目  (name, py_value, mt5_value, error)
    passed:     全項目が許容誤差内か
    """

    matches: list = field(default_factory=list)
    mismatches: list = field(default_factory=list)
    passed: bool = True


def _relative_error(py_value: float, mt5_value: float) -> float:
    """相対誤差。mt5_value == 0 のとき絶対差を返す（相対誤差が定義不能のため）。"""
    if mt5_value == 0:
        return abs(py_value - mt5_value)
    return abs(py_value - mt5_value) / abs(mt5_value)


def compare_stats(*, py_stats: dict, mt5_stats: dict, tolerances: dict) -> ComparisonReport:
    """tolerances に列挙されたキーを突合する。

    各キーについて相対誤差（mt5 が 0 なら絶対差）を許容値以下なら一致、超過なら不一致。
    突合キーが py_stats / mt5_stats に存在しない場合は KeyError を送出する。
    """
    report = ComparisonReport()
    for name, tol in tolerances.items():
        py_value = py_stats[name]   # 欠落時 KeyError（UC-003 例外ケース）
        mt5_value = mt5_stats[name]
        error = _relative_error(py_value, mt5_value)
        if error <= tol:
            report.matches.append((name, py_value, mt5_value))
        else:
            report.mismatches.append((name, py_value, mt5_value, error))
    report.passed = len(report.mismatches) == 0
    return report
