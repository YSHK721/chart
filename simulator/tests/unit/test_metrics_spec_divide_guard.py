"""ISSUE-395 / A-7: ドローダウン % の除算が 0 除算を踏まないことを固定する。

`np.where(cond, a / b, fallback)` は **分岐の前に両辺を評価する**ため、`b` に 0 を
含むと選ばれない側の `0/0` を必ず計算し `RuntimeWarning: invalid value encountered
in divide` を発生させる。値は `where` が fallback を選ぶため正しいが、警告は
「除算そのものが実行された」実証であり、症状の抑制（警告フィルタ）ではなく
**除算を条件付きにする**ことでしか除去できない。

`math_calculations` 経路（`Model=3`）は `deposit` が inert のため
`initial_deposit=0.0` で走り、`peak=[0.0]` となる。すなわち本件は例外系ではなく
**正常系で毎回**踏む欠陥である。
"""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from simulator.usecase import metrics_spec
from simulator.usecase.metrics_spec import _dd_arrays


def _runtime_warnings(fn):
    """`fn()` を実行し、発生した RuntimeWarning のみを返す（値も併せて返す）。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # numpy は seterr が 'warn' のときにのみ Python warning を出す（既定 'warn'）。
        with np.errstate(all="warn"):
            value = fn()
    return value, [w for w in caught if issubclass(w.category, RuntimeWarning)]


class TestDdArraysDoesNotDivideByZero:
    """peak に 0 を含む入力で、除算そのものが実行されないことを測る。"""

    def test_zero_peak_emits_no_runtime_warning(self):
        # initial_deposit=0.0・balance_curve=[0.0] は math_calculations の実形状。
        (_, dd_pct), caught = _runtime_warnings(lambda: _dd_arrays([0.0], 0.0))

        assert caught == [], f"0 除算が実行された: {[str(w.message) for w in caught]}"
        assert dd_pct.tolist() == [0.0, 0.0]

    def test_zero_peak_mixed_with_nonzero_keeps_values(self):
        # peak が 0 から立ち上がる系列: 0 の位置は 0.0、それ以外は通常の % DD。
        (dd_abs, dd_pct), caught = _runtime_warnings(
            lambda: _dd_arrays([100.0, 50.0], 0.0)
        )

        assert caught == []
        # _full_balance = [0, 100, 50] / peak = [0, 100, 100] / dd_abs = [0, 0, 50]
        assert dd_abs.tolist() == [0.0, 0.0, 50.0]
        assert dd_pct.tolist() == [0.0, 0.0, 50.0]

    def test_nonzero_peak_result_is_unchanged_from_where_formula(self):
        # 是正の等価性: peak != 0 の全要素で旧式 np.where と bit 単位一致すること。
        curve = [1000.0, 900.0, 1200.0, 600.0, 1500.0]
        deposit = 1000.0
        dd_abs, dd_pct = _dd_arrays(curve, deposit)

        b = np.asarray([deposit, *curve], dtype=float)
        peak = np.maximum.accumulate(b)
        legacy = np.where(peak != 0, (peak - b) / peak * 100.0, 0.0)

        assert np.array_equal(dd_pct, legacy)
        assert np.array_equal(dd_abs, peak - b)

    def test_dd_percent_accessors_are_warning_free_on_zero_peak(self):
        # 公開アクセサ 3 点も同じ経路を通る（_dd_arrays 経由）。
        for accessor in (
            metrics_spec.balance_dd_maximal_percent,
            metrics_spec.balance_dd_relative_percent,
            metrics_spec.balance_dd_relative_amount,
        ):
            value, caught = _runtime_warnings(lambda a=accessor: a([0.0], 0.0))
            assert caught == [], f"{accessor.__name__} で 0 除算: {caught}"
            assert value == 0.0


class TestNoUnguardedWhereDivision:
    """同型の欠陥（np.where の中で除算する）が本モジュールに残っていないこと。"""

    def test_module_source_has_no_where_wrapped_division(self):
        import inspect
        import re

        source = inspect.getsource(metrics_spec)
        offenders = []
        for line in source.splitlines():
            code = re.sub(r"#.*$", "", line)  # コメントは対象外（欠陥の説明文を誤検出しない）
            if "np.where(" in code and "/" in code.split("np.where(", 1)[1]:
                offenders.append(line.strip())
        assert offenders == [], f"np.where 内の除算が残存: {offenders}"


@pytest.mark.parametrize("deposit", [0.0, -100.0])
def test_non_positive_deposit_is_warning_free(deposit):
    """peak が 0 以下から始まる系列全般で警告 0 件（境界値）。"""
    _, caught = _runtime_warnings(lambda: _dd_arrays([0.0, 0.0], deposit))
    assert caught == []
