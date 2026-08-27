"""ISSUE-395 / A-5: `BacktestController` の interactor 公開取得点。

`run_from_settings` は「検証した request をそのまま実行する」ため
（`controller.run` は `market_data.load` を再実行して `trading_start` を落とす）、
インタラクタへ直接到達する必要がある。到達手段が非公開属性 `_interactor` だと
カプセル化が破れ、controller 側の内部表現変更が呼び出し側を無言で壊す。

本テストは以下 2 点を固定する:
  1. `BacktestController` が公開の取得点 `interactor` を提供すること（注入した実体を
     そのまま返す＝同一性まで固定し、差し替えは拒む）。
  2. `run_from_settings` の到達が公開取得点のみで成立すること。文字列 grep ではなく、
     `_interactor` を**持たない** controller ダブルを実経路へ差し込んで振る舞いで測る
     （`test_controller_interactor_access_wiring.py` が実経路側を担う）。

`run()` の挙動は本件で一切変えない（既存 `test_backtest_controller.py` が無改変で
通ることを合格条件とする）。
"""
from __future__ import annotations

import pytest

from simulator.adapter.controller import BacktestController
from simulator.usecase.ports import MarketDataPort, RunBacktestInputBoundary


#: `BacktestController.run` が組む `RunBacktestRequest.leverage`（ISSUE-445 段階 3-D2 で
#: 口座属性として必須化・既定値なし）。本モジュールのスタブ Interactor は request を
#: 解釈しないため、この値は観測される結果に一切影響しない（証拠金計算を通らない）。
_UNUSED_LEVERAGE = 1.0

class _StubMarketData(MarketDataPort):
    def __init__(self):
        self.loaded = []

    def load(self, source_ref, timeframe=None, period=None):
        self.loaded.append(source_ref)
        return []


class _StubInteractor(RunBacktestInputBoundary):
    def __init__(self):
        self.executed = []

    def execute(self, request):
        self.executed.append(request)
        return "executed"


def _controller(interactor=None):
    return BacktestController(
        market_data=_StubMarketData(), interactor=interactor or _StubInteractor()
    )


class TestPublicAccessor:
    def test_exposes_injected_interactor(self):
        interactor = _StubInteractor()

        # 同一性まで固定する（ラップ・再生成したものを返してはならない）。
        assert _controller(interactor).interactor is interactor

    def test_is_read_only(self):
        # 差し替えを許すと「構築時に注入する」という DI 契約が壊れる。
        controller = _controller()
        with pytest.raises(AttributeError):
            controller.interactor = _StubInteractor()

    def test_accessor_has_no_side_effect_on_run(self):
        # 取得点は参照のみ（ロードも実行も起こさない）。
        interactor = _StubInteractor()
        controller = _controller(interactor)

        controller.interactor
        controller.interactor

        assert interactor.executed == []
        assert controller._market_data.loaded == []

    def test_run_still_delegates_to_the_same_interactor(self):
        # `run()` の挙動不変（公開取得点の追加が委譲先を変えていないこと）。
        interactor = _StubInteractor()
        controller = _controller(interactor)

        code = controller.run(object(), "data.csv", leverage=_UNUSED_LEVERAGE)

        assert code == 0
        assert len(interactor.executed) == 1
        assert controller.interactor is interactor
