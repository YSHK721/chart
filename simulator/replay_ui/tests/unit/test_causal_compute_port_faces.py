"""CausalComputePort を役割別の 3 面へ分割する（ISP・ISSUE-479 Wave2 3-1 / S-5）。

なぜ必要か:
    `CausalComputePort` は 6 メソッドの単一 Protocol で、client は全員が全面を要求する型で
    受け取っていた。実際に使う面はそれぞれ狭い——窓を採るだけの ``_seq_chart_window`` は
    ``load_source`` しか呼ばず、足内の各時点を計算する _seq_steps_over_h_window は
    ``compute`` しか呼ばない。広い型で受けると「その client が何を必要としているか」が
    型に現れず、Decorator を書くときに落とした面が実行時まで露見しない。

固定する規則:
    1. 3 面（ロード面 / 時間足グリッド面 / 指標計算面）が互いに素で、その和が
       **usecase が実際に呼ぶ面**と過不足なく一致する（穴も死に面も作らない）。
       基準を合併 Protocol に置かないのは、合併が 3 面から派生するため等式が恒真式に
       なるからである（実測済み。詳細は _port_methods_called_by_the_usecase の docstring）。
    2. 狭い実装は狭い面としてだけ通り、合併 Protocol としては通らない（ISP の実効）。
    3. 単一面しか使わない usecase ヘルパは、その面で受けると宣言する。

計算量検定（絶対命令 2026-08-28）: 非投影経路の ``causal_compute`` は 1 呼び出しあたり
    源ロードを 1 回だけ発行する（発行 − 使用 = 0）。limit を変えた 2 点で発行が変わらない
    ことも固定する（発行は出力量ではなく「必要な窓の数」だけで決まる）。回数リテラルは
    焼き込まず、必要な窓の数から導出する。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from simulator.replay_ui.usecase import causal_compute as uc
from simulator.replay_ui.usecase.causal_compute import CausalComputeRequest
from simulator.replay_ui.usecase.replay_ports import (
    CausalComputePort,
    IndicatorComputePort,
    SourceLoadPort,
    TimeframeGridPort,
)

#: 面の宣言表（分割の仕様そのもの）。実装ではなく役割の境界を書いている。
_FACES = {
    SourceLoadPort: {"load_source"},
    TimeframeGridPort: {"bar_time", "period_start"},
    IndicatorComputePort: {"causal_series", "compute", "compute_latest_seq"},
}


def _methods(protocol) -> "set[str]":
    return set(protocol.__protocol_attrs__)


# --------------------------------------------------------------------------------------
# 1. 面の分割（互いに素・和が合併に一致）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("face", list(_FACES), ids=lambda f: f.__name__)
def test_each_face_declares_only_the_methods_of_its_role(face) -> None:
    assert _methods(face) == _FACES[face]


def _face_union() -> "set[str]":
    union: "set[str]" = set()
    for face in _FACES:
        union |= _methods(face)
    return union


def _port_methods_called_by_the_usecase() -> "set[str]":
    """usecase が compute_port に対して実際に呼ぶメソッド名（AST・独立な基準）。

    なぜ合併 Protocol と突き合わせないか（**実測した恒真式**）:
        合併 Protocol は 3 面を継承して定義されるため、面から 1 メソッドを落とすと合併からも
        同時に消える。したがって「3 面の和 == 合併の面」は常に真で、検定として何も守らない
        （実測: TimeframeGridPort から period_start を落とす変異でこの等式は破れなかった）。
        網羅の基準は分割の外＝**client が実際に要求する面**に置く必要がある。
    """
    tree = ast.parse(Path(inspect.getsourcefile(uc)).read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "compute_port"
    }


def test_the_faces_cover_exactly_what_the_usecase_asks_for() -> None:
    """3 面の和が、usecase が実際に呼ぶ面と過不足なく一致する（穴も死に面も作らない）。

    識別力: usecase が新しい Port メソッドを呼び始めたのにどの面へも足していなければ赤。
    逆に、どの client も呼ばない面を宣言したままにしても赤（分割が現実から離れる）。
    """
    assert _face_union() == _port_methods_called_by_the_usecase()


def test_the_merged_port_adds_no_method_of_its_own() -> None:
    """合併 Protocol は面を 1 つも増やさない（ここへ直接足すと分割が形骸化する）。"""
    assert _methods(CausalComputePort) == _face_union()


def test_the_faces_do_not_overlap() -> None:
    """同じメソッドが 2 つの面に属さない（重なりを作らない）。"""
    declared = [name for face in _FACES for name in _methods(face)]
    assert len(declared) - len(set(declared)) == 0, sorted(declared)


# --------------------------------------------------------------------------------------
# 2. 狭い実装は狭い面としてだけ通る（ISP の実効）
# --------------------------------------------------------------------------------------
class _OnlyLoads:
    """ロード面しか持たない実装（記憶 Decorator の素）。"""

    def load_source(self, ref, timeframe):
        return []


class _FullPort:
    """合併 Protocol の全面を持つ実装。"""

    def load_source(self, ref, timeframe):
        return []

    def bar_time(self, timeframe, unix_sec):
        return int(unix_sec)

    def period_start(self, timeframe, unix_sec):
        return int(unix_sec)

    def causal_series(self, indicator, variant, chart_bars, source_bars, compute_tf,
                      window_bars, params):
        return []

    def compute(self, indicator, variant, mode, bars, params):
        return []

    def compute_latest_seq(self, indicator, variant, prefix_bars, tails, params):
        return []


def test_a_load_only_implementation_passes_only_the_load_face() -> None:
    narrow = _OnlyLoads()
    assert isinstance(narrow, SourceLoadPort)
    assert not isinstance(narrow, TimeframeGridPort)
    assert not isinstance(narrow, IndicatorComputePort)
    assert not isinstance(narrow, CausalComputePort)


def test_a_full_implementation_passes_every_face() -> None:
    full = _FullPort()
    assert isinstance(full, CausalComputePort)
    for face in _FACES:
        assert isinstance(full, face), face.__name__


# --------------------------------------------------------------------------------------
# 3. 単一面しか使わない usecase ヘルパは、その面で受けると宣言する
# --------------------------------------------------------------------------------------
#: ヘルパ → 宣言すべき面（実際に呼ぶメソッドから決まる）。
_NARROWED = {
    "_seq_chart_window": "SourceLoadPort",
    "_seq_steps_over_h_window": "IndicatorComputePort",
}


@pytest.mark.parametrize("helper", sorted(_NARROWED))
def test_a_single_face_helper_declares_that_face(helper: str) -> None:
    """広い型で受けたままだと「何を必要としているか」が型に現れない。"""
    annotation = inspect.signature(getattr(uc, helper)).parameters["compute_port"].annotation
    assert _NARROWED[helper] in str(annotation), (helper, annotation)


def test_the_narrowed_helpers_still_run_with_a_full_port() -> None:
    """注釈を狭めても実行時の値は変わらない（注釈は実行時に効かない）。"""
    bars = uc._seq_chart_window(
        ref="jp225", timeframe="5m", until_time=None, limit=None,
        compute_port=_FullPort(),
    )
    assert bars == []


# --------------------------------------------------------------------------------------
# 4. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
class _CountingPort(_FullPort):
    """源ロードの発行だけを数える Spy。"""

    def __init__(self, bars) -> None:
        self._bars = bars
        self.loads: "list[tuple[str, str | None]]" = []

    def load_source(self, ref, timeframe):
        self.loads.append((ref, timeframe))
        return [dict(b) for b in self._bars]


_BARS = [
    {"time": t, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
    for t in (60, 120, 180, 240)
]


@pytest.mark.parametrize("limit", [1, 4], ids=["limit_1", "limit_4"])
def test_the_compute_path_loads_each_needed_window_once(limit: int) -> None:
    """limit 1 / 4 の 2 点で「源ロード発行 − 必要な窓の数 = 0」。

    非投影経路が必要とする窓は (ref, timeframe) の 1 つだけである。limit は出力量を
    変えるが必要な窓の数は変えない——発行が limit で動くなら、それは作って捨てている。
    """
    # Arrange
    port = _CountingPort(_BARS)
    request = CausalComputeRequest(
        indicator="ma", variant="default", ref="jp225", timeframe="5m",
        limit=limit, until_time=None, mode="full", forming=None, params={},
    )
    # Act
    uc.causal_compute(request=request, compute_port=port)
    # Assert（期待値は「必要な窓の数」から導出する。回数リテラルを焼き込まない）
    needed_windows = {(request.ref, request.timeframe)}
    assert len(port.loads) - len(needed_windows) == 0, port.loads
