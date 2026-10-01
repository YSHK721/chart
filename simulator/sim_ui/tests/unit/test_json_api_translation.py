"""JSON API の出口の共有部品（非有限値の null 化・失敗 → HTTP 状態の翻訳）。

分析 API（`/trace`）と足の API（`/chart-bars`）が**同じ実体**を使う。写しを 2 つ持つと、
片方だけ直る形で必ず食い違う（ISSUE-552/554 段階 2-1）。

固定する契約:
    1. 失敗の翻訳表（404 / 409 / 413 / 400）。表に無い例外は握らない。
    2. 分析 API の controller が使う消毒器は共有の実体そのもの（写しではない）。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.adapter import json_api_translation as shared
from simulator.sim_ui.usecase.job_models import JobNotFoundError, ResultNotAvailableError


class _Missing(Exception):
    pass


class _TooWide(Exception):
    pass


def _raise(exc):
    def call():
        raise exc
    return call


@pytest.mark.parametrize(
    "exc, status",
    [
        (JobNotFoundError("無い"), 404),
        (_Missing("成果物が無い"), 404),
        (ResultNotAvailableError("未完了"), 409),
        (_TooWide("広すぎる"), 413),
        (ValueError("不正"), 400),
    ],
)
def test_each_failure_maps_to_its_status_with_the_reason(exc, status) -> None:
    got = shared.guarded(_raise(exc), missing=(_Missing,), too_wide=(_TooWide,))
    assert got == (status, {"error": str(exc)})


def test_a_success_passes_through_unchanged() -> None:
    assert shared.guarded(lambda: (200, {"ok": True}), missing=(), too_wide=()) == (
        200, {"ok": True},
    )


def test_an_undeclared_failure_is_not_swallowed() -> None:
    with pytest.raises(KeyError):
        shared.guarded(_raise(KeyError("x")), missing=(_Missing,), too_wide=(_TooWide,))


def test_non_finite_floats_become_null_at_any_depth() -> None:
    got = shared.json_safe({"a": float("inf"), "b": [1.0, float("nan"), {"c": (float("-inf"),)}]})
    assert got == {"a": None, "b": [1.0, None, {"c": [None]}]}


def test_the_trace_controller_uses_the_shared_sanitiser_itself() -> None:
    import simulator.sim_ui.adapter.trace_api_controller as trace

    assert trace._json_safe is shared.json_safe
