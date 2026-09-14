"""刻み量子化の発行予算（計算量テスト・ISSUE-502 D-13）。

**測るのは時間ではなく回数**。所有者 ``volume_step`` へ委譲した各呼び出し側が、
*出力に必要な量子化だけを発行し、作ってから捨てない* ことを Test Spy で表明する。

固定するのは「無駄の不在」であって実装詳細ではない。期待値は
``出力の仕様から導いた「量子化が必要な入力の並び」`` であり、回数リテラルを焼き込まない：

    発行した量子化の入力列 == 出力に必要な量子化の入力列   （差 = 0）

この形は「作ってから捨てる」欠陥を捕える。出力は正しいままなので状態検証では
原理的に落ちない（例: トリガー未達なのに先に量子化する／``volume_step <= 0`` の
早期 return より前に量子化する）。

**委譲の証明も兼ねる**: Spy は各呼び出し側モジュールの束縛名を差し替える。呼び出し側が
量子化を自前で書き直すと Spy が 0 件になり、本検定が赤になる（構造ゲート
``test_volume_step_single_ownership.py`` と二重に塞ぐ）。

**オーダーの表明**: 入力の大きさ（volume を 6 桁・step を 4 桁変える 2 点以上）を変えても
発行数が増えないこと＝入力非依存（O(1)）を固定する。
"""
from __future__ import annotations

import pytest

from simulator.domain import order as order_module
from simulator.domain import partial_close_rule as pcr_module
from simulator.domain import volume_step as vs_module
from simulator.domain.exceptions import InvalidPriceError
from simulator.domain.order import Order
from simulator.domain.partial_close_rule import PartialCloseRule


class _QuantizeSpy:
    """所有者が公開する量子化・刻み判定の発行を記録する Test Spy。"""

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self.inputs: "list[tuple[float, float]]" = []

    def __call__(self, value, step):
        self.inputs.append((value, step))
        return self._wrapped(value, step)


def _spy_on(monkeypatch, module, name) -> _QuantizeSpy:
    spy = _QuantizeSpy(getattr(module, name))
    monkeypatch.setattr(module, name, spy)
    return spy


class _Spec:
    def __init__(self, volume_step: float):
        self.volume_min = 0.0
        self.volume_max = 1e12
        self.volume_step = volume_step
        self.stops_level = 0
        self.point_size = 0.01


# --- floor_to_step -----------------------------------------------------------

@pytest.mark.parametrize(
    "volume, step, maximum",
    [
        (1.2345, 0.01, 100.0),        # 通常
        (999.0, 0.10, 5.05),          # 上限で切り詰められる
        (1.2345e9, 1e-4, 1e12),       # 桁を大きく振る（オーダーの表明）
    ],
)
def test_floor_to_step_issues_exactly_the_quantization_its_output_needs(
    monkeypatch, volume, step, maximum
):
    # Arrange: 出力の仕様から必要な量子化を導く（回数リテラルを焼き込まない）。
    #   floor_to_step の出力は「min(volume, maximum) を step で量子化した値」1 つだけ。
    required = [(min(volume, maximum), step)]
    spy = _spy_on(monkeypatch, vs_module, "quantize_to_step")

    # Act
    vs_module.floor_to_step(volume, step=step, minimum=0.01, maximum=maximum)

    # Assert: 発行 − 使用 = 0
    assert spy.inputs == required


def test_floor_to_step_issues_nothing_when_the_input_is_rejected_before_quantizing(monkeypatch):
    # volume <= 0 は量子化を要しない（作ってから捨てる経路が無いこと）。
    spy = _spy_on(monkeypatch, vs_module, "quantize_to_step")

    assert vs_module.floor_to_step(0.0, step=0.1, minimum=0.01, maximum=100.0) is None
    assert spy.inputs == []


# --- PartialCloseRule.close_volume -------------------------------------------

def _rule(close_fraction: float = 0.5) -> PartialCloseRule:
    return PartialCloseRule(trigger_profit_points=50, close_fraction=close_fraction, point_size=0.1)


@pytest.mark.parametrize(
    "position_volume, volume_step",
    [
        (0.10, 0.01),
        (1.0e6, 1.0e-3),  # 桁を大きく振っても発行は増えない（オーダーの表明）
    ],
)
def test_close_volume_issues_exactly_the_quantization_its_output_needs(
    monkeypatch, position_volume, volume_step
):
    # Arrange: 作動時の出力は「保有量 × 割合」を刻みで量子化した値 1 つだけ。
    rule = _rule()
    required = [(position_volume * rule.close_fraction, volume_step)]
    spy = _spy_on(monkeypatch, pcr_module, "quantize_to_step")

    # Act（含み益 6.0 >= trigger 5.0）
    rule.close_volume("buy", 100.0, 106.0, position_volume, volume_step)

    # Assert
    assert spy.inputs == required


def test_close_volume_issues_nothing_before_the_trigger_is_reached(monkeypatch):
    # トリガー未達は量子化を要しない。先に量子化して捨てる実装なら赤になる。
    spy = _spy_on(monkeypatch, pcr_module, "quantize_to_step")

    assert _rule().close_volume("buy", 100.0, 104.0, 0.10, 0.01) is None
    assert spy.inputs == []


# --- Order._validate_volume ---------------------------------------------------

@pytest.mark.parametrize(
    "volume, step",
    [
        (0.10, 0.01),
        (1.0e6, 1.0e-4),  # 桁を大きく振っても発行は増えない（オーダーの表明）
    ],
)
def test_validate_issues_exactly_the_step_check_its_verdict_needs(monkeypatch, volume, step):
    # Arrange: 判定に必要な刻み検査は「発注量 × 銘柄刻み」の 1 件だけ。
    required = [(volume, step)]
    spy = _spy_on(monkeypatch, order_module, "is_step_multiple")

    # Act
    Order(side="buy", kind="market", volume=volume, price=None).validate(_Spec(step))

    # Assert
    assert spy.inputs == required


def test_validate_issues_nothing_when_the_symbol_has_no_step_constraint(monkeypatch):
    # volume_step <= 0 は「刻み制約なし」（ISSUE-445 段階 3-C）。検査を発行してはならない。
    spy = _spy_on(monkeypatch, order_module, "is_step_multiple")

    Order(side="buy", kind="market", volume=0.37, price=None).validate(_Spec(0.0))

    assert spy.inputs == []


def test_validate_issues_nothing_when_the_volume_is_out_of_range(monkeypatch):
    # 範囲違反で先に例外になる経路では刻み検査を発行しない。
    spy = _spy_on(monkeypatch, order_module, "is_step_multiple")
    spec = _Spec(0.01)
    spec.volume_max = 1.0

    with pytest.raises(InvalidPriceError):
        Order(side="buy", kind="market", volume=5.0, price=None).validate(spec)

    assert spy.inputs == []
