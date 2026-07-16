"""AssessmentPolicy（IS/OOS 合否方法論）単体テスト（ISSUE-094 🟡-5）。

degradation の ratio/delta・None 規則、verdict 判定木 4 分岐と reason 追加 2 条件、
および閾値注入時の境界挙動・reason 文言追従を固定する。既定閾値は現行値（§5.3）。
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from simulator.report_ui.usecase.assessment_policy import AssessmentPolicy
from simulator.report_ui.usecase.report_models import VerdictModel


def _summary(**kw):
    base = dict(net=0.0, profit_factor=1.0, win_rate=50.0, expectancy=0.0,
                payoff=1.0, return_pct=0.0, max_dd_pct=0.0)
    base.update(kw)
    return NS(**base)


# ---- degradation ----------------------------------------------------------

def test_degradation_ratio_and_delta():
    deg = AssessmentPolicy().degradation(_summary(net=100.0), _summary(net=50.0))
    assert deg["net"]["is"] == 100.0
    assert deg["net"]["oos"] == 50.0
    assert deg["net"]["ratio"] == 0.5
    assert deg["net"]["delta"] == -50.0


def test_degradation_ratio_none_when_is_zero():
    deg = AssessmentPolicy().degradation(_summary(net=0.0), _summary(net=5.0))
    assert deg["net"]["ratio"] is None
    assert deg["net"]["delta"] == 5.0


def test_degradation_key_order_is_spec_order():
    deg = AssessmentPolicy().degradation(_summary(), _summary())
    assert list(deg.keys()) == [
        "net", "profit_factor", "win_rate", "expectancy",
        "payoff", "return_pct", "max_dd_pct",
    ]


def test_degradation_custom_keys_injected():
    pol = AssessmentPolicy(deg_keys=("net", "payoff"))
    deg = pol.degradation(_summary(), _summary())
    assert list(deg.keys()) == ["net", "payoff"]


# ---- verdict 判定木（順序厳守）--------------------------------------------

def test_verdict_fail_is_profit_oos_loss():
    sis = _summary(net=100.0)
    soos = _summary(net=-50.0, profit_factor=0.5)
    pol = AssessmentPolicy()
    v = pol.verdict(sis, soos, pol.degradation(sis, soos))
    assert isinstance(v, VerdictModel)
    assert v.result == "fail"
    assert "優位性消失" in v.reasons[0]


def test_verdict_fail_oos_pf_below_floor():
    sis = _summary(net=-10.0, profit_factor=2.0)
    soos = _summary(net=5.0, profit_factor=0.5)
    pol = AssessmentPolicy()
    v = pol.verdict(sis, soos, pol.degradation(sis, soos))
    assert v.result == "fail"
    assert v.reasons[0] == "OOS PF=0.500<1.0＝検証区間で損失超過"


def test_verdict_warn_pf_ratio_below_threshold():
    sis = _summary(net=100.0, profit_factor=10.0)
    soos = _summary(net=50.0, profit_factor=1.2)  # ratio 0.12 < 0.7
    pol = AssessmentPolicy()
    v = pol.verdict(sis, soos, pol.degradation(sis, soos))
    assert v.result == "warn"
    assert v.reasons[0] == "PF劣化 比=0.12（OOS/IS<0.7）"


def test_verdict_pass_when_robust():
    s = _summary(net=100.0, profit_factor=10.0)
    pol = AssessmentPolicy()
    v = pol.verdict(s, s, pol.degradation(s, s))
    assert v.result == "pass"
    assert v.reasons[0] == "OOSでも優位性を維持"


# ---- reason 追加 2 条件 ----------------------------------------------------

def test_verdict_winrate_delta_reason_added():
    sis = _summary(net=100.0, profit_factor=10.0, win_rate=100.0)
    soos = _summary(net=50.0, profit_factor=10.0, win_rate=50.0)  # delta -50 < -5
    pol = AssessmentPolicy()
    v = pol.verdict(sis, soos, pol.degradation(sis, soos))
    assert any("勝率差" in r for r in v.reasons)


def test_verdict_expectancy_reversal_reason_added():
    sis = _summary(net=100.0, profit_factor=10.0, expectancy=5.0)
    soos = _summary(net=50.0, profit_factor=10.0, expectancy=-2.0)  # ratio<0
    pol = AssessmentPolicy()
    v = pol.verdict(sis, soos, pol.degradation(sis, soos))
    assert any("期待値が正→負へ反転" in r for r in v.reasons)


# ---- 閾値境界（注入時の追従）----------------------------------------------

def test_pf_warn_threshold_boundary_not_triggered_at_equal():
    # ratio == pf_warn_ratio は "< threshold" 不成立 → warn にならない（pass）。
    sis = _summary(net=100.0, profit_factor=10.0)
    soos = _summary(net=50.0, profit_factor=7.0)  # ratio 0.7 == 閾値
    pol = AssessmentPolicy()
    v = pol.verdict(sis, soos, pol.degradation(sis, soos))
    assert v.result == "pass"


def test_injected_oos_pf_floor_changes_decision_and_reason():
    # floor=2.0 を注入: OOS PF=1.5 < 2.0 → fail、文言も注入値へ追従。
    sis = _summary(net=-10.0, profit_factor=3.0)
    soos = _summary(net=5.0, profit_factor=1.5)
    pol = AssessmentPolicy(oos_pf_floor=2.0)
    v = pol.verdict(sis, soos, pol.degradation(sis, soos))
    assert v.result == "fail"
    assert v.reasons[0] == "OOS PF=1.500<2.0＝検証区間で損失超過"


def test_injected_winrate_floor_boundary():
    # winrate_delta_floor=-3 注入: delta -4 < -3 → reason 追加。
    sis = _summary(net=100.0, profit_factor=10.0, win_rate=60.0)
    soos = _summary(net=50.0, profit_factor=10.0, win_rate=56.0)  # delta -4
    pol = AssessmentPolicy(winrate_delta_floor=-3.0)
    v = pol.verdict(sis, soos, pol.degradation(sis, soos))
    assert any("勝率差" in r for r in v.reasons)
