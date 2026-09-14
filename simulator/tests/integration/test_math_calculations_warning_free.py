"""ISSUE-395 / A-7: `Math calculations` の正常実行が RuntimeWarning を出さないこと。

`Model=3` は `deposit` が inert のため `initial_deposit=0.0` で走る（推定値を発明
しない設計・`math_calculations.INERT_DEPOSIT`）。その結果 balance の peak 系列が
`0.0` になり、`metrics_spec._dd_arrays` の % DD 計算が 0 除算を踏んでいた。

本テストは「正常系の実行で警告が 1 件も出ない」ことを固定する。警告フィルタでの
抑制は本テストを通すが、それは症状の隠蔽であり、通過条件は **除算が実行されない**
こと（`test_metrics_spec_divide_guard.py` が式の側を固定する）と対で成立する。
"""
from __future__ import annotations

import warnings

import numpy as np

from simulator.main.tester_settings.math_calculations import run_math_calculations
from simulator.tests.tester_settings_engine_fixtures import engine_binding, runnable_settings

#: `Model=3`（`MATH_CALCULATIONS`）。既存 `test_math_calculations_run.py` と同一値。
_MATH_MODEL = "3"


def test_math_calculations_emits_no_runtime_warning():
    effective = runnable_settings(Model=_MATH_MODEL).effective()
    binding = engine_binding(data_path=None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with np.errstate(all="warn"):
            exit_code, result, _meta = run_math_calculations(effective, binding)

    runtime_warnings = [
        f"{w.category.__name__}: {w.message} ({w.filename}:{w.lineno})"
        for w in caught
        if issubclass(w.category, RuntimeWarning)
    ]

    assert exit_code == 0
    assert result is not None
    assert runtime_warnings == [], f"正常系で警告が出た: {runtime_warnings}"


def test_math_calculations_zero_deposit_dd_fields_are_zero():
    """警告除去が値を変えていないこと（0 除算の fallback と同値の 0.0 のまま）。"""
    effective = runnable_settings(Model=_MATH_MODEL).effective()
    _code, result, _meta = run_math_calculations(effective, engine_binding(data_path=None))

    stats = result.stats
    assert stats.initial_deposit == 0.0
    assert stats.balance_dd == 0.0
    assert stats.balance_dd_percent == 0.0
    assert stats.balance_dd_relative == 0.0
    assert stats.balance_ddrel_percent == 0.0
