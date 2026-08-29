"""計算量 2（§7 §5.5 固有）: 発行した前進評価 − 係数決定に使った前進評価 = 0。

§5.5.4 実測: 係数決定は instance あたり 区分数 × 3 回（marod 9 回 / RSI 18 回）。ラダー 82 行の
評価は**発行 0 回**（参照実装を直接呼ぶなら 2,050 回）。閉形式と直接呼び出しの最大差 1.1e-08。

**回数そのもの（9 や 18）は期待値に焼き込まない。** 固定するのは無駄の不在である。
"""
from __future__ import annotations

from dashboard_ui.domain.bar import Bar
from dashboard_ui.tests.complexity.conftest import ForwardSpy, Registry
from dashboard_ui.usecase.sheet_models import SheetInstance
from dashboard_ui.usecase.update_reach_sheet import refresh_projection

_INSTANCE = SheetInstance("ma_marod", "default", {"length": 24}, "1m",
                          intrabar_capable=True)


def _bar(**over) -> Bar:
    base = dict(time=1_700_000_000, open=100.0, high=110.0, low=90.0, close=100.0)
    base.update(over)
    return Bar(**base)


def _refresh(cache, bar, forward, instances=(_INSTANCE,)):
    return refresh_projection(cache, forming_bar=bar, instances=instances,
                              dataset_ref="jp225_tick", forward_port=forward,
                              registry=Registry({"ma_marod"}))


def test_every_forward_evaluation_was_spent_on_a_coefficient() -> None:
    """発行 − 使用 = 0。検算用の追加探針など、係数に使われない発行があってはならない。"""
    forward = ForwardSpy()

    cache = _refresh(None, _bar(), forward)

    coefficients_determined = len(cache.maps[_INSTANCE.key].pieces)
    # 係数は 3 つ（a, b, d）なので 1 区分の決定に必要な最小の探針数は 3。
    assert len(forward.calls) - coefficients_determined * 3 == 0


def test_evaluating_the_ladder_spends_nothing_more() -> None:
    """閉形式の評価は前進評価を 1 回も発行しない（参照実装の直接呼びとの差がここ）。"""
    forward = ForwardSpy()
    cache = _refresh(None, _bar(), forward)
    spent_on_fit = len(forward.calls)

    price_value_map = cache.maps[_INSTANCE.key]
    for step in range(200):
        price_value_map.value_at(90.0 + step * 0.1)

    assert len(forward.calls) == spent_on_fit


def test_the_fit_is_not_repeated_for_the_same_epoch() -> None:
    forward = ForwardSpy()
    cache = _refresh(None, _bar(), forward)
    spent_on_fit = len(forward.calls)

    for _ in range(50):
        cache = _refresh(cache, _bar(), forward)

    assert len(forward.calls) == spent_on_fit
