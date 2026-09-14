"""`ExecutionMode` の実証状態を 1 箇所で宣言することを固定する（🟡-5 の是正）。

背景（実測）:
    `kwargs_mapper.MEASURED_EXECUTION_DELAYS`（「**実測済み**の遅延」の意）は
    `vars(ExecutionDelay)` の名前付き定数を機械的に全部集めていた。ところが
    `ExecutionDelay` の 2 定数は実証状態が異なる（`enums.py`）:

        ZERO_LATENCY_IDEAL = 0   # 暫定（TBD-08。画像 1 のラベル対応は未取得）
        DELAY_50MS         = 50  # 実証（golden fixture の delays_ms=50 と一致）

    結果として `ExecutionMode=0` は `approximate=False` / `reasons=()` になり、
    **未実証の値が「近似ではない」として呼出側へ伝わっていた**（実測:
    `Model=1, ExecutionMode=0` → `approximate=False reasons=()`）。

固定する仕様:
    1. 実証済みとして扱う遅延は `DELAY_50MS` のみ。
    2. `ZERO_LATENCY_IDEAL`（0）は近似であり、理由に TBD-08 を積む。
    3. 名前を持たない値（corpus 実測の -1 / 21 等）は従来どおり近似。
    4. 実証状態の宣言は `ExecutionDelay` を定義する
       `simulator.usecase.tester_settings.enums` の**1 箇所**のみ（定数とその実証状態は
       同じ 1 つの事実の 2 面であり、置き場所を分けない）。`kwargs_mapper` は判定を
       持たず、この宣言を読むだけである。
    5. `ExecutionDelay` に定数が増えたとき、実証状態を宣言し忘れたら落ちる
       （網羅ゲート）。宣言漏れが「暗黙に実証済み扱い」へ倒れる事故を防ぐ。
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

from simulator.main.tester_settings.kwargs_mapper import build_run_metadata
from simulator.tests.tester_settings_engine_fixtures import runnable_settings
from simulator.usecase.tester_settings import (
    PROVEN_EXECUTION_DELAYS,
    PROVISIONAL_EXECUTION_DELAYS,
    ExecutionDelay,
    approximation_reason_for,
)

#: `Model` のうち近似ではないもの（N-06 は `EVERY_TICK` だけに付く）。遅延の近似判定
#: だけを取り出して測るために使う。
_NON_APPROXIMATE_MODEL = "1"  # ONE_MINUTE_OHLC


def _metadata(execution_mode: str, model: str = _NON_APPROXIMATE_MODEL):
    return build_run_metadata(
        runnable_settings(ExecutionMode=execution_mode, Model=model).effective()
    )


def _named_execution_delays() -> "dict[str, int]":
    """`ExecutionDelay` が名前を与えている定数（名前 → 値）。"""
    return {
        name: value
        for name, value in vars(ExecutionDelay).items()
        if not name.startswith("_") and isinstance(value, int)
    }


class TestEvidenceDeclaration:
    """宣言表そのもの（実証済み / 暫定）。"""

    def test_only_the_fifty_millisecond_delay_is_proven(self):
        assert PROVEN_EXECUTION_DELAYS == frozenset({ExecutionDelay.DELAY_50MS})

    def test_the_zero_latency_value_is_declared_provisional_with_its_tbd(self):
        assert PROVISIONAL_EXECUTION_DELAYS[ExecutionDelay.ZERO_LATENCY_IDEAL] == "TBD-08"

    def test_every_named_constant_has_an_evidence_status(self):
        # 網羅ゲート: 定数を足して実証状態を書き忘れたら落ちる
        classified = PROVEN_EXECUTION_DELAYS | frozenset(PROVISIONAL_EXECUTION_DELAYS)
        assert frozenset(_named_execution_delays().values()) == classified

    def test_proven_and_provisional_do_not_overlap(self):
        assert PROVEN_EXECUTION_DELAYS & frozenset(PROVISIONAL_EXECUTION_DELAYS) == frozenset()


class TestApproximationReason:
    """値 1 個 → 近似理由（`None` なら近似ではない）。"""

    def test_a_proven_delay_has_no_reason(self):
        assert approximation_reason_for(ExecutionDelay.DELAY_50MS) is None

    def test_an_absent_delay_has_no_reason(self):
        # inert（規則 A）で値が無い場合は近似の主張をしない
        assert approximation_reason_for(None) is None

    def test_a_provisional_delay_carries_its_tbd_number(self):
        reason = approximation_reason_for(ExecutionDelay.ZERO_LATENCY_IDEAL)
        assert reason is not None and "TBD-08" in reason

    @pytest.mark.parametrize("delay", [-1, 21])
    def test_an_unnamed_delay_is_approximate(self, delay):
        reason = approximation_reason_for(delay)
        assert reason is not None and str(delay) in reason


class TestRunMetadataUsesTheDeclaration:
    """実行メタ情報（呼出側へ届く面）に宣言が効いていること。"""

    def test_zero_execution_mode_is_reported_as_approximate(self):
        metadata = _metadata("0")
        assert metadata.approximate is True

    def test_zero_execution_mode_reports_its_tbd(self):
        assert any("TBD-08" in reason for reason in _metadata("0").approximation_reasons)

    def test_the_measured_delay_is_not_reported_as_approximate(self):
        metadata = _metadata("50")
        assert metadata.approximate is False
        assert metadata.approximation_reasons == ()

    def test_an_unnamed_delay_is_still_reported_as_approximate(self):
        assert _metadata("21").approximate is True

    def test_the_every_tick_reason_is_unaffected(self):
        # N-06 は tick_model 由来。遅延の是正がこれを消していないこと。
        assert "N-06" in _metadata("50", model="0").approximation_reasons


class TestTheDeclarationLivesBesideTheConstants:
    """凝集: 実証状態は `ExecutionDelay` を宣言するモジュール（`enums`）が持つ。

    「その定数が実証済みか」は定数そのものと同じ 1 つの事実の 2 面であり、置き場所を
    分けると片方だけが更新される。同一概念に 2 つの置き場所を作らない。
    """

    def test_the_enums_module_declares_the_evidence_sets(self):
        # Arrange / Act
        enums_mod = importlib.import_module("simulator.usecase.tester_settings.enums")
        # Assert
        assert hasattr(enums_mod, "PROVEN_EXECUTION_DELAYS")
        assert hasattr(enums_mod, "PROVISIONAL_EXECUTION_DELAYS")

    def test_the_enums_module_declares_the_reason_function(self):
        enums_mod = importlib.import_module("simulator.usecase.tester_settings.enums")
        assert hasattr(enums_mod, "approximation_reason_for")

    def test_no_separate_evidence_module_remains(self):
        # 置き場所が 2 つある状態そのものを落とす
        assert (
            importlib.util.find_spec(
                "simulator.usecase.tester_settings.execution_delay_evidence"
            )
            is None
        )


class TestSingleJudgementSite:
    """判定を 2 箇所に持たない（`kwargs_mapper` は宣言を読むだけ）。"""

    def test_the_mapper_does_not_enumerate_the_delay_constants_itself(self):
        source = Path(
            __import__(
                "simulator.main.tester_settings.kwargs_mapper", fromlist=["__file__"]
            ).__file__
        ).read_text(encoding="utf-8")
        # `vars(ExecutionDelay)` を自前で走査していれば、実証状態の判定が 2 箇所になる
        assert "vars(ExecutionDelay)" not in source

    def test_the_mapper_declares_no_delay_set_of_its_own(self):
        path = Path(
            __import__(
                "simulator.main.tester_settings.kwargs_mapper", fromlist=["__file__"]
            ).__file__
        )
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assigned: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assigned.add(node.target.id)
            elif isinstance(node, ast.Assign):
                assigned.update(t.id for t in node.targets if isinstance(t, ast.Name))
        assert "MEASURED_EXECUTION_DELAYS" not in assigned
