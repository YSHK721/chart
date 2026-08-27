"""`run_backtest` が `controller.execute` へ委譲する契約を固定する（ISSUE-398）。

背景（是正前の形）:
    `run_backtest` は `controller.run(request.config, meta["data_path"], ...)` を呼び、
    結果を `getattr(controller.interactor, "last_result", None)` で拾っていた。この形は
      (a) `build_interactor` が読んだ bars を捨てて同じファイルを再読込する（二重ロード）
      (b) `run()` が `RunBacktestRequest` を組み直すため `trading_start` が黙って落ちる
    の 2 つを抱えていた。是正後は `controller.execute(request)` の戻り値を直接使う。

なぜこのモジュールが要るか:
    数値指紋（`tests/integration/test_run_backtest_fingerprint.py`）は**成功経路**しか
    測れない。「実行段が `BacktestError` を送出したときの (終了コード, 結果) が
    是正前と同じか」は指紋の射程外であり、この置き換えの byte 等価を主張する上で
    最も落としやすい穴である。ここを実行して固定する。

    実測（是正時）: 是正前ロジック（`controller.run` ＋ `last_result` 読み出し）と
    是正後の実コードは、`ConfigError` で `(2, None)`、`DataError` で `(1, None)` と
    完全に一致した。本モジュールはその一致を検定として残す。
"""
from __future__ import annotations

import pytest

import simulator.main as simulator_main
from simulator.adapter.controller import BacktestController
from simulator.usecase.models import AccountSpec
from simulator.adapter.exit_codes import SUCCESS_EXIT_CODE, exit_code_for
from simulator.domain.exceptions import ConfigError, DataError


#: `BacktestController.run` が組む `RunBacktestRequest.account`（ISSUE-445 段階 3-D3 で
#: 口座の契約 1 型に束ね、既定値をどのフィールドにも置かない）。本モジュールのスタブ
#: Interactor は request を解釈しないため、これらの値は観測される結果に一切影響しない
#: （証拠金計算を通らない）。
_UNUSED_ACCOUNT = AccountSpec(initial_deposit=0.0, leverage=1.0, stop_out_level=0.0)

class _MarketData:
    """`BacktestController` が要求する最小の `MarketDataPort` 代役。"""

    def __init__(self) -> None:
        self.loads = 0

    def load(self, source_ref, timeframe=None, period=None):
        self.loads += 1
        return []


class _Request:
    """`run_backtest` の成功経路が参照する属性だけを持つ最小の代役。"""

    config = None
    symbol_spec = None
    # 口座の契約は 1 つの型に束ねた（ISSUE-445 段階 3-D3）。`run_backtest` は request の
    # 中身を読まないため、代役は形だけを持つ。
    account = None
    bars: "list" = []
    trading_start = None


class _RaisingInteractor:
    def __init__(self, error: BaseException) -> None:
        self._error = error
        #: 是正前が読んでいた属性。実行が例外で抜けるため常に None のままである。
        self.last_result = None

    def execute(self, request):
        raise self._error


class _RecordingInteractor:
    def __init__(self, result) -> None:
        self._result = result
        self.seen: "list" = []

    def execute(self, request):
        self.seen.append(request)
        return self._result


def _patch(monkeypatch, interactor, market_data=None) -> "tuple[object, _Request]":
    """`build_interactor` を差し替え、その controller/request を返す。"""
    market_data = market_data or _MarketData()
    controller = BacktestController(market_data=market_data, interactor=interactor)
    request = _Request()
    monkeypatch.setattr(
        simulator_main, "build_interactor", lambda **meta: (controller, request)
    )
    return controller, request


class TestTheExecutionStageIsTranslatedLikeBefore:
    """実行段の例外が是正前と同じ (終了コード, 結果) になること。"""

    @pytest.mark.parametrize(
        "error",
        [ConfigError("設定不正"), DataError("実行時")],
        ids=["ConfigError", "DataError"],
    )
    def test_a_backtest_error_from_execute_maps_to_the_shared_table(
        self, error, monkeypatch
    ):
        _patch(monkeypatch, _RaisingInteractor(error))
        exit_code, result = simulator_main.run_backtest(data_path="-", output_dir=None)
        # 値は書き写さず、唯一の宣言場所へ問い合わせて突合する。
        assert exit_code == exit_code_for(error)
        assert result is None

    def test_a_non_backtest_error_is_not_swallowed(self, monkeypatch):
        # 未知の失敗を終了コードへ化けさせない（既存規約）。
        _patch(monkeypatch, _RaisingInteractor(RuntimeError("内部不整合")))
        with pytest.raises(RuntimeError):
            simulator_main.run_backtest(data_path="-", output_dir=None)


class TestTheResultComesFromExecute:
    """結果は `execute` の戻り値であり `last_result` の読み直しではないこと。"""

    def test_the_returned_result_is_the_value_execute_returned(self, monkeypatch):
        sentinel = object()
        # `last_result` 属性を**持たない**インタラクタでも結果が返る。是正前は
        # `getattr(..., "last_result", None)` に依存していたため None になっていた。
        interactor = _RecordingInteractor(sentinel)
        assert not hasattr(interactor, "last_result")
        _patch(monkeypatch, interactor)
        exit_code, result = simulator_main.run_backtest(data_path="-", output_dir=None)
        assert exit_code == SUCCESS_EXIT_CODE
        assert result is sentinel

    def test_the_request_built_by_build_interactor_is_the_one_executed(self, monkeypatch):
        """検証した request と実行する request が同一インスタンスであること。

        是正前は `controller.run` が request を**組み直していた**ため、
        `build_interactor` が組んだ request（`trading_start` を持つ）は実行されなかった。
        """
        interactor = _RecordingInteractor(object())
        _controller, request = _patch(monkeypatch, interactor)
        simulator_main.run_backtest(data_path="-", output_dir=None)
        assert interactor.seen == [request], "組立済 request がそのまま実行されていない"


class TestTheMarketDataIsLoadedOnlyOnce:
    """二重ロードが消えたこと（`run_backtest` は再読込しない）。"""

    def test_run_backtest_does_not_reload_the_source(self, monkeypatch):
        market_data = _MarketData()
        _patch(monkeypatch, _RecordingInteractor(object()), market_data=market_data)
        simulator_main.run_backtest(data_path="-", output_dir=None)
        # `build_interactor` を差し替えているのでこの代役の load 回数は 0 が正しい。
        # 是正前は `controller.run` がここで 1 回読み直していた（＝二重ロード）。
        assert market_data.loads == 0

    def test_the_controller_run_path_still_reloads(self):
        """対照: `controller.run` は従来どおり読み直す（本検定が空振りでないこと）。"""
        market_data = _MarketData()
        controller = BacktestController(
            market_data=market_data, interactor=_RecordingInteractor(object())
        )
        controller.run(None, "-", symbol_spec=None, account=_UNUSED_ACCOUNT)
        assert market_data.loads == 1
