"""TDD 単体: optimize UC コア（失敗候補除外・best0件・run 回数 N_cand+1・best_is_stats
保持・excluded_count 内訳・空区間中断）（詳細設計 §6.2.4・C-1/M-1/High-2）。

合成 run_segment スタブ（params -> 固定 stats）＋呼出回数カウンタでエンジン非依存に検証。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from simulator.domain.exceptions import ConfigError, MarginCallError
from simulator.usecase.optimize import (
    OptimizeError,
    OptimizeRequest,
    optimize,
)
from simulator.usecase.optimize_strategies import GridSearch, NetProfitObjective


@dataclass
class _Bar:
    time: int


@dataclass
class _Stats:
    profit: float = 0.0
    profit_factor: float = 0.0
    recovery_factor: float = 0.0
    expected_payoff: float = 0.0
    sharpe_ratio: float = 0.0
    trades: int = 0


def _bars(n: int = 10, split: int = 5):
    # time 0..n-1。split=5 -> IS=[0..4] 5件 / OOS=[5..9] 5件
    return [_Bar(time=i) for i in range(n)]


def _request(search_space, *, split=5, is_trading_start=0):
    return OptimizeRequest(
        search_space=search_space, split=split, is_trading_start=is_trading_start
    )


class _Factory:
    """params -> run_segment。run_segment 呼出（IS/OOS）回数を記録。

    stats_for(params) で各候補の固定 stats を返す。raise_for に param 値を渡すと
    その候補の run_segment 呼出時に指定例外を送出する。
    """

    def __init__(self, stats_map: dict, raise_map: dict | None = None):
        self.stats_map = stats_map
        self.raise_map = raise_map or {}
        self.run_calls: list[tuple] = []  # (param_value, n_bars, trading_start)

    def __call__(self, params: dict):
        key = params["x"]

        def run_segment(bars: Any, trading_start: Any):
            self.run_calls.append((key, len(list(bars)), trading_start))
            if key in self.raise_map:
                raise self.raise_map[key]
            return self.stats_map[key]

        return run_segment


def test_failed_candidate_with_margin_call_is_excluded_and_continues():
    # Arrange: 候補 x=2 で MarginCallError。best は残り候補から確定（M-1）
    stats = {1: _Stats(profit=100.0), 3: _Stats(profit=300.0)}
    factory = _Factory(stats, raise_map={2: MarginCallError("stop out")})
    req = _request({"x": [1, 2, 3]})

    # Act
    result = optimize(
        request=req, full_bars=_bars(),
        make_run_segment=factory,
        search_port=GridSearch(max_candidates=99),
        objective_port=NetProfitObjective(),
    )

    # Assert: 失敗候補は failed 記録・除外、best は x=3
    failed = [t for t in result.trials if t.failed]
    assert len(failed) == 1
    assert "MarginCallError" in failed[0].failure_reason
    assert result.best_params == {"x": 3}
    assert result.excluded_count == {"nonfinite": 0, "failed": 1}


def test_config_error_candidate_is_excluded_and_continues():
    # Arrange: 候補 x=1 で ConfigError（M-1）
    stats = {2: _Stats(profit=200.0)}
    factory = _Factory(stats, raise_map={1: ConfigError("bad cfg")})
    req = _request({"x": [1, 2]})

    # Act
    result = optimize(
        request=req, full_bars=_bars(),
        make_run_segment=factory,
        search_port=GridSearch(max_candidates=99),
        objective_port=NetProfitObjective(),
    )

    # Assert
    assert result.best_params == {"x": 2}
    assert result.excluded_count["failed"] == 1


def test_all_candidates_failed_raises_optimize_error_with_zero_finite():
    # Arrange: 全候補が失敗（best 0 件・C-1/M-1）
    factory = _Factory({}, raise_map={1: ConfigError("a"), 2: MarginCallError("b")})
    req = _request({"x": [1, 2]})

    # Act / Assert
    with pytest.raises(OptimizeError) as exc:
        optimize(
            request=req, full_bars=_bars(),
            make_run_segment=factory,
            search_port=GridSearch(max_candidates=99),
            objective_port=NetProfitObjective(),
        )
    assert exc.value.context["finite_candidates"] == 0
    assert exc.value.context["excluded_failed"] == 2


def test_all_candidates_nonfinite_raises_optimize_error():
    # Arrange: 全候補が非有限スコア（profit=inf -> 除外）（C-1）
    stats = {1: _Stats(profit=float("inf")), 2: _Stats(profit=float("nan"))}
    factory = _Factory(stats)
    req = _request({"x": [1, 2]})

    # Act / Assert
    with pytest.raises(OptimizeError) as exc:
        optimize(
            request=req, full_bars=_bars(),
            make_run_segment=factory,
            search_port=GridSearch(max_candidates=99),
            objective_port=NetProfitObjective(),
        )
    assert exc.value.context["excluded_nonfinite"] == 2
    assert exc.value.context["finite_candidates"] == 0


def test_run_count_is_n_candidates_plus_one_with_no_best_is_rerun():
    # Arrange: 3 候補・全成功。IS run = 3 回 + OOS run = 1 回 = 4 回（High-2/R-O5）
    stats = {1: _Stats(profit=100.0), 2: _Stats(profit=200.0), 3: _Stats(profit=300.0)}
    factory = _Factory(stats)
    req = _request({"x": [1, 2, 3]})

    # Act
    optimize(
        request=req, full_bars=_bars(),
        make_run_segment=factory,
        search_port=GridSearch(max_candidates=99),
        objective_port=NetProfitObjective(),
    )

    # Assert: run_segment 総呼出 = N_cand + 1（best の IS 再 run なし）
    assert len(factory.run_calls) == 4
    # IS run は各候補 1 回ずつ（3 回）＋ OOS（best=x:3）1 回
    is_calls = [c for c in factory.run_calls if c[2] == req.is_trading_start]
    oos_calls = [c for c in factory.run_calls if c[2] == req.split]
    assert len(is_calls) == 3
    assert len(oos_calls) == 1
    assert oos_calls[0][0] == 3  # OOS は best=x:3 で実行


def test_best_is_stats_is_retained_instance_not_rerun():
    # Arrange: best の is_stats が探索中に保持したインスタンスと同一（再 run で別物にならない・High-2）
    best_stats = _Stats(profit=300.0)
    stats = {1: _Stats(profit=100.0), 3: best_stats}
    factory = _Factory(stats)
    req = _request({"x": [1, 3]})

    # Act
    result = optimize(
        request=req, full_bars=_bars(),
        make_run_segment=factory,
        search_port=GridSearch(max_candidates=99),
        objective_port=NetProfitObjective(),
    )

    # Assert: 同一インスタンス（保持値採用）
    assert result.best_is_stats is best_stats


def test_excluded_count_breakdown_separates_nonfinite_and_failed():
    # Arrange: nonfinite 2 件・failed 1 件・finite 1 件
    stats = {
        1: _Stats(profit=float("inf")),
        2: _Stats(profit=float("nan")),
        4: _Stats(profit=400.0),
    }
    factory = _Factory(stats, raise_map={3: MarginCallError("x")})
    req = _request({"x": [1, 2, 3, 4]})

    # Act
    result = optimize(
        request=req, full_bars=_bars(),
        make_run_segment=factory,
        search_port=GridSearch(max_candidates=99),
        objective_port=NetProfitObjective(),
    )

    # Assert
    assert result.excluded_count == {"nonfinite": 2, "failed": 1}
    assert result.total_candidates == 4
    assert result.finite_candidates == 1
    assert result.best_params == {"x": 4}


def test_empty_is_segment_raises_pre_validation_optimize_error():
    # Arrange: split=0 -> IS 区間（bar.time < 0）が 0 件
    factory = _Factory({1: _Stats(profit=1.0)})
    req = _request({"x": [1]}, split=0, is_trading_start=0)

    # Act / Assert
    with pytest.raises(OptimizeError) as exc:
        optimize(
            request=req, full_bars=_bars(),
            make_run_segment=factory,
            search_port=GridSearch(max_candidates=99),
            objective_port=NetProfitObjective(),
        )
    assert exc.value.context["phase"] == "pre_validation"


def test_argmax_tie_first_wins_in_enumeration_order():
    # Arrange: x=1 と x=3 が同一スコア 100.0（列挙順先勝ち＝x:1）（NFR-OD1）
    stats = {1: _Stats(profit=100.0), 2: _Stats(profit=50.0), 3: _Stats(profit=100.0)}
    factory = _Factory(stats)
    req = _request({"x": [1, 2, 3]})

    # Act
    result = optimize(
        request=req, full_bars=_bars(),
        make_run_segment=factory,
        search_port=GridSearch(max_candidates=99),
        objective_port=NetProfitObjective(),
    )

    # Assert: tie は先出（x:1）が勝つ（厳密 > のみ更新）
    assert result.best_params == {"x": 1}
