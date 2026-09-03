"""run_backtest の 2 エンジン 1 本化（ISSUE-479 Wave2 フェーズ 4・S-1）の構造検定。

固定する仕様:
    bar 経路（既定）と tick 経路（every-tick）が、**同じ 1 つの定義点**を
    通って走ること。段階が進むごとに共有される段（終了段・状態初期化・成行約定・
    建玉変更・評価点決済…）が増え、本ファイルはそのつど「両経路が同じ点を通る」ことと
    「その点が無駄に発行されない」ことを固定する。

なぜ構造を検定するか:
    2 つのエンジンが同じ処理を**書き写して**持っている限り、片方だけが更新される形の
    欠陥は「どちらのエンジンも自分の検定では緑」のまま通過する。数値の指紋（G0-a）は
    その食い違いを、**両経路が同じ fixture を通ったときにしか**捉えられない。
    定義点が 1 つであることを直接固定すれば、食い違いは構造的に起こり得なくなる。

計算量（プロジェクト絶対命令 2026-08-28）:
    測るのは時間ではなく回数。Test Spy で発行回数を数え「発行 − 使用 = 0」を表明し、
    さらに入力量（バー数）を変えた 2 点で「発行が出力量だけで決まる」ことを固定する。
    回数そのものは期待値に焼き込まない（焼き込むと浪費が仕様へ昇格する）。
"""
from __future__ import annotations

import numpy as np
import pytest

from simulator.domain.bar import Bar
from simulator.usecase import run_backtest as rb
from simulator.usecase.models import AccountSpec, BacktestConfig, SymbolSpec
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest


# ---- 最小の合成 Port（本ファイルは「構造」を測るので値は動かさない） ----

class _NullIndicators:
    def get(self, name):
        return None

    def update(self, bar_index):
        return None


class _NullStrategy:
    """発注しない戦略（約定の有無は本ファイルの関心ではない）。"""

    def on_init(self, config, indicators):
        return None

    def on_new_bar(self, bar_index, indicators, account):
        return []

    def on_position_check(self, position, bar_index, indicators):
        return "hold"

    def on_tick(self, bar_index, bid, ask, account):
        return []


class _OneTickPerBar:
    def ticks_of(self, bar, prev_close):
        return [(bar.close, bar.low, bar.high, bar.time)]


def _bars(count: int) -> "list[Bar]":
    """`count` 本の単調な合成足（値そのものには意味を持たせない）。"""
    out = []
    for i in range(count):
        base = 1.10 + i * 0.001
        out.append(
            Bar(
                time=np.datetime64("2024-01-01T00:00") + np.timedelta64(i, "m"),
                open=base,
                high=base + 0.01,
                low=base - 0.01,
                close=base + 0.005,
                volume=1.0,
                spread=0,
            )
        )
    return out


def _config(**overrides):
    base = dict(
        tick_model="ohlc_simulate",
        spread_model="fixed",
        sltp_tie="sl",
        fill_delay="next_tick",
        ohlc_order="auto",
        session_calendar="none",
        digits=5,
        legacy_quirks=False,
        return_basis="equity",
    )
    base.update(overrides)
    return BacktestConfig(**base)


def _request(bars, *, config=None):
    return RunBacktestRequest(
        config=config or _config(),
        bars=bars,
        symbol_spec=SymbolSpec(
            contract_size=1.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            stops_level=0,
            digits=5,
            point_size=0.00001,
        ),
        account=AccountSpec(
            initial_deposit=10_000.0, leverage=100.0, stop_out_level=0.0
        ),
    )


def _interactor(*, log=None, session_calendar=None):
    return RunBacktestInteractor(
        strategy=_NullStrategy() if log is None else _LoggingStrategy(log),
        indicators=_NullIndicators(),
        tick_model=_OneTickPerBar(),
        session_calendar=session_calendar,
    )


class _LoggingStrategy(_NullStrategy):
    """準備段の呼出順を記録する戦略 Spy。"""

    def __init__(self, log):
        self._log = log

    def on_init(self, config, indicators):
        self._log.append("strategy.on_init")


class _LoggingCalendar:
    """準備段の呼出順を記録するカレンダー Spy（常時開場＝空集合）。"""

    def __init__(self, log):
        self._log = log

    def closed_bar_indices(self, bars):
        self._log.append("calendar.closed_bar_indices")
        return set()


#: 「bar 経路」「tick 経路」を 1 つの request 形で切り替える（両経路に同じ検定を当てる）。
_BAR_PATH = ("bar", {})
_TICK_PATH = ("tick", {"tick_model": "real_ticks"})
_BOTH_PATHS = [_BAR_PATH, _TICK_PATH]


def _run(bars, path_overrides, *, spy_on=None, monkeypatch=None):
    """1 run 実行し、`spy_on` に挙げた `run_backtest` モジュール属性の発行回数を返す。

    事後条件: `(result, {属性名: 発行回数})`。
    """
    counts: "dict[str, int]" = {}
    if spy_on:
        for name in spy_on:
            original = getattr(rb, name)
            counts[name] = 0

            def _wrapped(*args, _name=name, _original=original, **kwargs):
                counts[_name] += 1
                return _original(*args, **kwargs)

            monkeypatch.setattr(rb, name, _wrapped)
    result = _interactor().execute(_request(bars, config=_config(**path_overrides)))
    return result, counts


# ---- 4-1: 終了段（OnDeinit 集計）の単一化 ----

class TestBothEnginesShareTheFinishStage:
    """終了段（統計の畳み込みと結果 DTO の組み立て）の定義点が 1 つであること。"""

    def test_the_finish_stage_has_exactly_one_definition_point(self):
        # Arrange / Act: 終了段は Interactor の 1 メソッドとして在る。
        # Assert: 両経路が同じ実体を呼べる（書き写しではない）。
        assert callable(getattr(RunBacktestInteractor, "_finish_run", None))

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_each_path_reaches_the_finish_stage_through_that_single_point(
        self, path, overrides, monkeypatch
    ):
        # Arrange: 終了段の呼び出しを記録する Spy を Interactor に被せる。
        calls: "list[str]" = []
        original = RunBacktestInteractor._finish_run

        def _spy(self, **kwargs):
            calls.append(path)
            return original(self, **kwargs)

        monkeypatch.setattr(RunBacktestInteractor, "_finish_run", _spy)
        # Act
        result = _interactor().execute(_request(_bars(8), config=_config(**overrides)))
        # Assert: 結果は終了段が組み立てたものであり、経路を問わず同じ点を通る。
        assert calls == [path]
        assert result.stats is not None

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_result_carries_the_curves_the_run_produced(self, path, overrides):
        # Arrange / Act
        result = _interactor().execute(_request(_bars(8), config=_config(**overrides)))
        # Assert: 終了段が 5 フィールドすべてを結果へ載せている（1 本でも落ちれば赤）。
        assert result.trades == []
        assert result.deals == []
        assert len(result.equity_curve) > 0
        assert result.balance_curve == []
        assert result.stats is not None


class TestTheFinishStageDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_statistics_are_folded_once_per_run_not_once_per_bar(
        self, path, overrides, monkeypatch
    ):
        # Arrange / Act: 統計畳み込みの発行を数える。
        result, counts = _run(
            _bars(16), overrides, spy_on=["compute_stats"], monkeypatch=monkeypatch
        )
        # Assert: 発行（畳み込み）− 使用（結果に載った統計 1 つ）= 0。
        used = 1 if result.stats is not None else 0
        assert counts["compute_stats"] - used == 0

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_fold_count_does_not_grow_with_the_number_of_bars(
        self, path, overrides, monkeypatch
    ):
        """バー数 50 / 200 の 2 点で発行数が変わらないこと（オーダーの表明）。"""
        # Arrange / Act
        measured = {}
        for bar_count in (50, 200):
            _, counts = _run(
                _bars(bar_count), overrides, spy_on=["compute_stats"], monkeypatch=monkeypatch
            )
            measured[bar_count] = counts["compute_stats"]
        # Assert: 入力を 4 倍にしても発行は増えない（バー数に非比例）。
        assert measured[200] - measured[50] == 0, measured


# ---- 4-2: 準備段（run 開始時の状態組み立て）の単一化 ----

class TestBothEnginesShareTheSetupStage:
    """run 開始時の状態（口座・記録先・セッション判定・区間設定）の組み立てが 1 点であること。"""

    def test_the_setup_stage_has_exactly_one_definition_point(self):
        assert callable(getattr(RunBacktestInteractor, "_begin_run", None))

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_session_gate_is_built_before_the_strategy_is_initialised(
        self, path, overrides
    ):
        """準備段の副作用の順序（セッション判定の導出 → 戦略の初期化）を固定する。

        なぜ順序が仕様か: 戦略の on_init は config を受け取って自分の状態を組む。
        その前にセッション判定（どのバーが閉鎖か）を確定しておかないと、準備段の
        途中で戦略が走り出した状態のまま閉鎖バー集合が組まれることになり、
        「戦略が見る世界」と「エンジンが使う世界」の成立順が run ごとに揺れうる。
        """
        # Arrange
        log: "list[str]" = []
        interactor = _interactor(log=log, session_calendar=_LoggingCalendar(log))
        # Act
        interactor.execute(_request(_bars(4), config=_config(**overrides)))
        # Assert: 準備段の副作用は run につき 1 回ずつ、この順で起きる。
        assert log == ["calendar.closed_bar_indices", "strategy.on_init"]

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_setup_stage_yields_the_state_the_run_starts_from(self, path, overrides):
        # Arrange
        request = _request(_bars(4), config=_config(**overrides))
        # Act
        state = _interactor()._begin_run(request)
        # Assert: run の開始状態が 1 つの値として在る（両経路で同じ形）。
        assert list(state.bars) == list(request.bars)
        assert state.spec is request.symbol_spec
        assert state.contract_size == request.symbol_spec.contract_size
        assert state.account.balance == request.account.initial_deposit
        assert (state.trades, state.deals, state.balance_curve, state.equity_curve) == (
            [], [], [], []
        )
        assert state.open_trades == []
        assert state.halted is False
        assert state.primed_done is False
        assert state.session_gate.closed_bars == set()
        assert callable(state.close_trade)


class TestTheSetupStageDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。"""

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_session_gate_is_derived_once_per_run_not_once_per_bar(
        self, path, overrides
    ):
        # Arrange / Act: 閉鎖バー集合の導出発行を数える。
        log: "list[str]" = []
        _interactor(log=log, session_calendar=_LoggingCalendar(log)).execute(
            _request(_bars(32), config=_config(**overrides))
        )
        # Assert: 発行（導出）− 使用（run が使う 1 つのセッション判定）= 0。
        issued = log.count("calendar.closed_bar_indices")
        assert issued - 1 == 0

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_setup_effort_does_not_grow_with_the_number_of_bars(self, path, overrides):
        """バー数 50 / 200 の 2 点で準備段の発行数が変わらないこと（オーダーの表明）。"""
        # Arrange / Act
        measured = {}
        for bar_count in (50, 200):
            log: "list[str]" = []
            _interactor(log=log, session_calendar=_LoggingCalendar(log)).execute(
                _request(_bars(bar_count), config=_config(**overrides))
            )
            measured[bar_count] = len(log)
        # Assert: 入力を 4 倍にしても準備段の発行は増えない。
        assert measured[200] - measured[50] == 0, measured
