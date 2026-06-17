"""UC-003 compare_stats: py_stats と mt5_stats(dict) を tolerances で照合する純粋比較。

DESIGN §1 許容誤差表: 件数（TRADES/PROFIT_TRADES 等）は完全一致、金額（PROFIT/DD 等）は
±0.5%。ComparisonReport{matches, mismatches, passed} を返す。
"""
from __future__ import annotations

import pytest


def _make_report(py: dict, mt5: dict, tol: dict):
    from backtest.usecase.compare_stats import compare_stats

    return compare_stats(py_stats=py, mt5_stats=mt5, tolerances=tol)


def test_all_within_tolerance_passes():
    py = {"STAT_PROFIT": 330.0, "STAT_TRADES": 10}
    mt5 = {"STAT_PROFIT": 331.0, "STAT_TRADES": 10}  # 0.3% < 0.5%
    tol = {"STAT_PROFIT": 0.005, "STAT_TRADES": 0.0}

    report = _make_report(py, mt5, tol)

    assert report.passed is True
    assert len(report.mismatches) == 0
    names = [m[0] for m in report.matches]
    assert "STAT_PROFIT" in names and "STAT_TRADES" in names


def test_amount_outside_tolerance_fails():
    py = {"STAT_PROFIT": 330.0}
    mt5 = {"STAT_PROFIT": 350.0}  # ~6% > 0.5%
    tol = {"STAT_PROFIT": 0.005}

    report = _make_report(py, mt5, tol)

    assert report.passed is False
    assert len(report.mismatches) == 1
    name, py_v, mt5_v, err = report.mismatches[0]
    assert name == "STAT_PROFIT"
    assert py_v == 330.0
    assert mt5_v == 350.0
    assert err == pytest.approx(abs(330.0 - 350.0) / abs(350.0), abs=1e-9)


def test_count_mismatch_fails_with_exact_match_tolerance():
    # 件数は完全一致（tolerance 0.0）。1 件差でも不一致
    py = {"STAT_TRADES": 10}
    mt5 = {"STAT_TRADES": 11}
    tol = {"STAT_TRADES": 0.0}

    report = _make_report(py, mt5, tol)

    assert report.passed is False
    assert report.mismatches[0][0] == "STAT_TRADES"


def test_count_exact_match_passes():
    py = {"STAT_TRADES": 10}
    mt5 = {"STAT_TRADES": 10}
    tol = {"STAT_TRADES": 0.0}

    report = _make_report(py, mt5, tol)

    assert report.passed is True


def test_tolerance_boundary_is_inclusive():
    # 誤差が許容と完全一致する境界値は一致とみなす（<= 判定）
    py = {"STAT_PROFIT": 100.0}
    mt5 = {"STAT_PROFIT": 100.5}  # err = 0.5/100.5 = 0.004975...
    tol = {"STAT_PROFIT": abs(100.0 - 100.5) / abs(100.5)}  # ちょうど境界

    report = _make_report(py, mt5, tol)

    assert report.passed is True
    assert len(report.mismatches) == 0


def test_just_over_boundary_fails():
    py = {"STAT_PROFIT": 100.0}
    mt5 = {"STAT_PROFIT": 100.5}
    tol = {"STAT_PROFIT": abs(100.0 - 100.5) / abs(100.5) - 1e-6}  # 境界をわずかに下回る

    report = _make_report(py, mt5, tol)

    assert report.passed is False


def test_missing_key_in_mt5_raises_key_error():
    # 突合キー欠落（CLEAN_ARCH §3 UC-003 例外ケース）
    from backtest.usecase.compare_stats import compare_stats

    py = {"STAT_PROFIT": 330.0}
    mt5 = {}  # キー欠落
    tol = {"STAT_PROFIT": 0.005}

    with pytest.raises(KeyError):
        compare_stats(py_stats=py, mt5_stats=mt5, tolerances=tol)


def test_zero_mt5_value_uses_absolute_difference():
    # mt5 値が 0 のとき相対誤差は定義不能。絶対差 == 0 なら一致、非 0 なら不一致
    py_match = {"STAT_GROSS_LOSS": 0.0}
    mt5 = {"STAT_GROSS_LOSS": 0.0}
    tol = {"STAT_GROSS_LOSS": 0.005}
    assert _make_report(py_match, mt5, tol).passed is True

    py_diff = {"STAT_GROSS_LOSS": 5.0}
    assert _make_report(py_diff, mt5, tol).passed is False


def test_only_keys_in_tolerances_are_compared():
    # tolerances に列挙されたキーのみ突合（突合項目は変更対象 CLEAN_ARCH §10）
    py = {"STAT_PROFIT": 330.0, "STAT_EXTRA": 999.0}
    mt5 = {"STAT_PROFIT": 330.0, "STAT_EXTRA": 1.0}
    tol = {"STAT_PROFIT": 0.005}  # STAT_EXTRA は突合対象外

    report = _make_report(py, mt5, tol)

    assert report.passed is True
    compared = [m[0] for m in report.matches] + [m[0] for m in report.mismatches]
    assert "STAT_EXTRA" not in compared


def test_compare_stats_module_purity():
    import ast

    import backtest.usecase.compare_stats as cs

    with open(cs.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("backtest.adapter", "backtest.framework", "backtest.main", "pydantic", "pandas")
    for name in imported:
        assert not name.startswith(forbidden), name
