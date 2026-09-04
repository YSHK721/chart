"""ISSUE-395 / A-5: `run_from_settings` の到達が公開取得点のみで成立すること。

`run_from_settings` は `controller.run` を使えない（`run` は `market_data.load` を
再実行して `RunBacktestRequest` を組み直すため、窓検証を通した request の
`trading_start` が落ちる）。そのため request を直接実行するが、その手段は
`BacktestController` の**公開**メソッド `execute` でなければならない
（ISSUE-398 以前は公開プロパティ `interactor` 経由だった）。

測り方: 実経路（実 CSV・実 `build_interactor`・実 `verify_window_applied`）を走らせ、
`build_interactor` が返す controller だけを「`_interactor` を持たないダブル」に
差し替える。非公開属性へ到達していれば `AttributeError` で落ちる。ソース文字列の
grep ではなく振る舞いで測るため、将来の書き換えでも意味が保たれる。
"""
from __future__ import annotations

from datetime import date
from importlib import import_module

import pytest

from simulator.main.tester_settings.run_from_settings import run_from_settings
from simulator.tests.tester_settings_engine_fixtures import (
    daily_epochs,
    engine_binding,
    runnable_settings,
    write_comma_csv,
)

#: monkeypatch 対象はモジュール実体（`__init__` の再エクスポートが属性を影にするため）。
run_module = import_module("simulator.main.tester_settings.run_from_settings")

FIRST_DAY = date(2024, 1, 1)
BAR_DAYS = 5


class _PublicOnlyController:
    """公開点のみを持つ controller ダブル（`_interactor` を意図的に持たない）。

    ISSUE-398 で公開の**実行点** `execute(request)` が加わり、`run_from_settings` は
    `controller.interactor.execute(...)` ではなく `controller.execute(...)` を呼ぶ。
    取得点 `interactor` も既存公開 API として残っているため、双方を備える。
    """

    def __init__(self, interactor):
        self.interactor = interactor

    def execute(self, request):
        return self.interactor.execute(request)

    def __getattr__(self, name):  # pragma: no cover - 到達したら設計違反
        raise AssertionError(
            f"run_from_settings が controller の非公開/想定外属性へ到達した: {name!r}"
        )


@pytest.fixture()
def csv_path(tmp_path):
    return write_comma_csv(tmp_path / "jp225_daily.csv", daily_epochs(FIRST_DAY, BAR_DAYS))


@pytest.fixture()
def public_only_controller(monkeypatch):
    """実 `build_interactor` の結果を公開取得点のみのダブルへ包み替える。"""
    real_build = run_module.build_interactor

    def wrapped(**kwargs):
        controller, request = real_build(**kwargs)
        # ここで `controller.interactor` が読めること自体が A-5 の公開取得点の実証。
        return _PublicOnlyController(controller.interactor), request

    monkeypatch.setattr(run_module, "build_interactor", wrapped)


def test_run_completes_without_private_attribute_access(csv_path, public_only_controller):
    exit_code, result, _metadata = run_from_settings(
        runnable_settings(Dates="0"), engine_binding(data_path=str(csv_path))
    )

    assert exit_code == 0
    assert result is not None


def test_private_attribute_double_would_fail_the_same_run(csv_path, monkeypatch):
    """対照: 公開取得点を持たない controller では落ちる（テストが空振りでないこと）。"""
    real_build = run_module.build_interactor

    class _PrivateOnlyController:
        def __init__(self, interactor):
            self._interactor = interactor

    def wrapped(**kwargs):
        controller, request = real_build(**kwargs)
        return _PrivateOnlyController(controller.interactor), request

    monkeypatch.setattr(run_module, "build_interactor", wrapped)

    with pytest.raises(AttributeError):
        run_from_settings(
            runnable_settings(Dates="0"), engine_binding(data_path=str(csv_path))
        )
