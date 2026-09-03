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


# ---- 4-3: run のスイッチ読み取り点の単一化（O-2） ----

def _run_backtest_tree():
    """実行経路の構文木（既定値リテラルの所在を測るため）。"""
    import ast
    from pathlib import Path

    source = Path(rb.__file__).read_text(encoding="utf-8")
    return ast.parse(source, filename=rb.__file__)


class TestTheRunSwitchesAreReadInOnePlace:
    """config 由来のスイッチが 1 点で読まれ、既定値が実行経路に書かれていないこと。"""

    def test_the_engine_holds_no_default_value_for_a_config_switch(self):
        """実行経路に `getattr(config, 名前, 既定)` の形が 1 つも無いこと。

        なぜこれを固定するか: 既定値リテラルが読み取り側に在ると、models.py の宣言を
        変えても追随せず、しかも 2 つのエンジンのうち片方だけを直すと両者が違う既定で
        走る。既定の所在を宣言 1 箇所に閉じたことを、構文木で機械的に施行する。
        """
        import ast

        offenders = [
            node.lineno
            for node in ast.walk(_run_backtest_tree())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 3
        ]
        assert offenders == [], (
            f"既定値付き getattr が実行経路に残っている（行: {offenders}）。"
            " 既定値の単一ソースは models.py の BacktestConfig 宣言である。"
        )

    def test_the_engine_does_not_read_config_attributes_directly(self):
        """スイッチの読み取りは RunFeatures 経由だけであること（`config.X` の直参照 0）。"""
        import ast

        offenders = [
            (node.lineno, node.attr)
            for node in ast.walk(_run_backtest_tree())
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "config"
        ]
        assert offenders == [], f"config の直参照が残っている: {offenders}"

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_run_state_carries_the_switches_the_run_was_started_with(
        self, path, overrides
    ):
        # Arrange / Act
        config = _config(**overrides)
        state = _interactor()._begin_run(_request(_bars(4), config=config))
        # Assert: run が見るスイッチ束は開始状態の一部として在る。
        for name in state.features.feature_names():
            assert getattr(state.features, name) == getattr(config, name), name

    def test_both_engines_are_driven_by_the_same_switch_set(self, monkeypatch):
        """bar 経路と tick 経路が同じ 1 つの読み取り点から供給されること。"""
        # Arrange: スイッチ束の生成を記録する。
        from simulator.usecase.run_features import RunFeatures as _RF

        built: "list[object]" = []
        original = _RF.of.__func__

        def _spy(cls, config):
            features = original(cls, config)
            built.append(features)
            return features

        monkeypatch.setattr(rb, "RunFeatures", type("_Spy", (_RF,), {"of": classmethod(_spy)}))
        # Act: tick 経路（分岐を経て内側エンジンへ入る）を 1 run 走らせる。
        _interactor().execute(_request(_bars(4), config=_config(tick_model="real_ticks")))
        # Assert: 分岐と内側エンジンは同じ束を使う（読み直しをしない）。
        assert len(built) == 1


class TestTheSwitchReadingDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。"""

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_config_is_read_once_per_switch_per_run(self, path, overrides):
        # Arrange: 属性アクセスを記録する config（分岐と内側エンジンの両方が読む）。
        reads: "list[str]" = []
        base = _config(**overrides)

        class _Counting:
            def __getattr__(self, name):
                reads.append(name)
                return getattr(base, name)

        # Act
        _interactor().execute(
            RunBacktestRequest(
                config=_Counting(),
                bars=_bars(24),
                symbol_spec=_request(_bars(1)).symbol_spec,
                account=_request(_bars(1)).account,
            )
        )
        # Assert: 発行（config からの読み取り）− 使用（スイッチの数）= 0。
        from simulator.usecase.run_features import RunFeatures as _RF

        assert len(reads) - len(_RF.feature_names()) == 0, reads

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_config_read_count_does_not_grow_with_the_number_of_bars(
        self, path, overrides
    ):
        """バー数 50 / 200 の 2 点で config 読み取り数が変わらないこと（オーダーの表明）。"""
        measured = {}
        for bar_count in (50, 200):
            reads: "list[str]" = []
            base = _config(**overrides)

            class _Counting:
                def __getattr__(self, name):
                    reads.append(name)
                    return getattr(base, name)

            _interactor().execute(
                RunBacktestRequest(
                    config=_Counting(),
                    bars=_bars(bar_count),
                    symbol_spec=_request(_bars(1)).symbol_spec,
                    account=_request(_bars(1)).account,
                )
            )
            measured[bar_count] = len(reads)
        assert measured[200] - measured[50] == 0, measured


# ---- 4-4: 証拠金割れの決定点の単一化（O-3） ----

def _margin_breach_request(*, config_overrides, bars):
    """1 lot 買いを建て、指定した足で証拠金を割らせる run（3 つの評価点で共用）。

    contract=100000・leverage=100 → 必要証拠金 1100。deposit 10,000・stop_out 50%。
    """
    from simulator.domain.order import Order

    spec = SymbolSpec(
        contract_size=100_000.0, volume_min=0.01, volume_max=100.0,
        volume_step=0.01, stops_level=0, digits=5, point_size=0.00001,
    )

    class _BuyOnce(_NullStrategy):
        def on_new_bar(self, bar_index, indicators, account):
            return [Order(side="buy", kind="market", volume=1.0, price=None)] if bar_index == 0 else []

    interactor = RunBacktestInteractor(
        strategy=_BuyOnce(), indicators=_NullIndicators(), tick_model=_OneTickPerBar()
    )
    request = RunBacktestRequest(
        config=_config(**config_overrides),
        bars=bars,
        symbol_spec=spec,
        account=AccountSpec(initial_deposit=10_000.0, leverage=100.0, stop_out_level=50.0),
    )
    return interactor, request


def _flat_then_crash_bars():
    return [
        Bar(time=np.datetime64("2024-01-01T00:00"), open=1.10, high=1.10, low=1.10,
            close=1.10, volume=1.0, spread=0),
        Bar(time=np.datetime64("2024-01-01T00:01"), open=1.10, high=1.10, low=1.00,
            close=1.00, volume=1.0, spread=0),
    ]


def _open_gap_bars():
    return [
        Bar(time=np.datetime64("2024-01-01T00:00"), open=1.10, high=1.10, low=1.10,
            close=1.10, volume=1.0, spread=0),
        Bar(time=np.datetime64("2024-01-01T00:01"), open=0.50, high=1.10, low=0.50,
            close=1.05, volume=1.0, spread=0),
    ]


#: 証拠金割れが起こりうる 3 つの評価点（バー open / バー close / ティック）。
#: 移設前はこの 3 点それぞれに同じ 2 分岐が書き写されていた。
_BREACH_SITES = [
    ("bar_open", {"entry_price_basis": "current_open", "stop_out_at_open": True}, _open_gap_bars),
    ("bar_close", {}, _flat_then_crash_bars),
    ("tick", {"tick_model": "real_ticks"}, _flat_then_crash_bars),
]


class TestTheMarginCallPayloadIsUnchanged:
    """送出される MarginCallError の中身を 3 評価点すべてで byte 単位に固定する。

    なぜ中身まで固定するか: この例外は run を捨てる合図であり、外側（CLI の終了コード
    翻訳・最適化ループの失敗記録）が message と context を読む。決定点を 1 つに束ねる
    改修で文言や診断値が動くと、外側の分類が静かにずれる。
    """

    @pytest.mark.parametrize(
        "site,overrides,make_bars", _BREACH_SITES, ids=[s[0] for s in _BREACH_SITES]
    )
    def test_the_default_action_raises_with_the_documented_payload(
        self, site, overrides, make_bars
    ):
        from simulator.domain.exceptions import MarginCallError

        # Arrange: stop_out_action 未指定＝既定 fail_stop。
        interactor, request = _margin_breach_request(
            config_overrides=overrides, bars=make_bars()
        )
        # Act
        with pytest.raises(MarginCallError) as excinfo:
            interactor.execute(request)
        # Assert: 文言・診断値・バー位置が移設前と同一。
        err = excinfo.value
        expected_message = "margin_level が stop_out_level を下回りました" + (
            "（bar open 評価）" if site == "bar_open" else ""
        )
        assert str(err) == expected_message
        assert set(err.context) == {"margin_level", "stop_out_level"}
        assert err.context["stop_out_level"] == 50.0
        assert err.context["margin_level"] < 50.0
        assert err.bar_index == 1

    @pytest.mark.parametrize(
        "site,overrides,make_bars", _BREACH_SITES, ids=[s[0] for s in _BREACH_SITES]
    )
    def test_close_and_halt_liquidates_instead_of_raising(self, site, overrides, make_bars):
        # Arrange
        interactor, request = _margin_breach_request(
            config_overrides={**overrides, "stop_out_action": "close_and_halt"},
            bars=make_bars(),
        )
        # Act
        result = interactor.execute(request)
        # Assert: 送出せず、保有玉が stop_out として確定し run が完走する。
        assert [t.exit_reason for t in result.trades] == ["stop_out"]

    @pytest.mark.parametrize(
        "site,overrides,make_bars", _BREACH_SITES, ids=[s[0] for s in _BREACH_SITES]
    )
    def test_an_unknown_action_falls_back_to_raising(self, site, overrides, make_bars):
        """綴り違いの設定が黙って完走しないこと（3 評価点すべてで同じ側へ落ちる）。"""
        from simulator.domain.exceptions import MarginCallError

        interactor, request = _margin_breach_request(
            config_overrides={**overrides, "stop_out_action": "clsoe_and_halt"},
            bars=make_bars(),
        )
        with pytest.raises(MarginCallError):
            interactor.execute(request)


class TestTheStopOutDecisionHasOneDefinitionPoint:
    """実行経路に方針名のリテラルが残っていないこと。"""

    def test_the_engine_holds_no_stop_out_action_literal(self):
        import ast

        offenders = [
            (node.lineno, node.value)
            for node in ast.walk(_run_backtest_tree())
            if isinstance(node, ast.Constant)
            and node.value in ("close_and_halt", "fail_stop")
        ]
        assert offenders == [], (
            f"方針名のリテラルが実行経路に残っている: {offenders}。"
            " 名前から決定への対応は stop_out_policy の表が単一ソースである。"
        )

    @pytest.mark.parametrize(
        "site,overrides,make_bars", _BREACH_SITES, ids=[s[0] for s in _BREACH_SITES]
    )
    def test_every_breach_site_goes_through_the_single_decision_point(
        self, site, overrides, make_bars, monkeypatch
    ):
        # Arrange: 決定点の呼び出しを記録する。
        from simulator.usecase import stop_out_policy as sop

        asked: "list[object]" = []
        original = sop.resolve_stop_out_policy("close_and_halt")

        class _Recording:
            def on_breach(self, ctx):
                asked.append(ctx)
                return original.on_breach(ctx)

        monkeypatch.setattr(rb, "resolve_stop_out_policy", lambda action: _Recording())
        interactor, request = _margin_breach_request(
            config_overrides=overrides, bars=make_bars()
        )
        # Act
        interactor.execute(request)
        # Assert: 割れは決定点へ問われ、その決定（強制決済）が実行された。
        assert len(asked) >= 1
        assert asked[0].stop_out_level == 50.0
        assert asked[0].bar_index == 1


class TestTheStopOutDecisionDoesNotWasteWork:
    """計算量検定（発行 − 使用 = 0）。"""

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_policy_is_resolved_once_per_run_not_once_per_bar(
        self, path, overrides, monkeypatch
    ):
        # Arrange: 方針の解決発行を数える（割れない run でも run につき 1 回）。
        from simulator.usecase import stop_out_policy as sop

        resolved: "list[str]" = []

        def _spy(action):
            resolved.append(action)
            return sop.resolve_stop_out_policy(action)

        monkeypatch.setattr(rb, "resolve_stop_out_policy", _spy)
        # Act
        _interactor().execute(_request(_bars(32), config=_config(**overrides)))
        # Assert: 発行（解決）− 使用（run が使う 1 つの方針）= 0。
        assert len(resolved) - 1 == 0

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_policy_resolution_does_not_grow_with_the_number_of_bars(
        self, path, overrides, monkeypatch
    ):
        """バー数 50 / 200 の 2 点で解決発行が変わらないこと（オーダーの表明）。"""
        from simulator.usecase import stop_out_policy as sop

        measured = {}
        for bar_count in (50, 200):
            resolved: "list[str]" = []
            monkeypatch.setattr(
                rb,
                "resolve_stop_out_policy",
                lambda action: (resolved.append(action), sop.resolve_stop_out_policy(action))[1],
            )
            _interactor().execute(_request(_bars(bar_count), config=_config(**overrides)))
            measured[bar_count] = len(resolved)
        assert measured[200] - measured[50] == 0, measured


# ---- 4-5: 成行約定（F）の単一化 ----

class _OrdersPerBar(_NullStrategy):
    """指定バーで指定の成行注文列を返す戦略。"""

    def __init__(self, orders_by_bar):
        self._orders_by_bar = orders_by_bar

    def on_new_bar(self, bar_index, indicators, account):
        return list(self._orders_by_bar.get(bar_index, []))


def _market(side):
    from simulator.domain.order import Order

    return Order(side=side, kind="market", volume=1.0, price=None)


def _fill_scenario(overrides, orders_by_bar):
    """同一バーに複数の成行注文を投げ、走査順と反映順の対応を測る run。"""
    interactor = RunBacktestInteractor(
        strategy=_OrdersPerBar(orders_by_bar),
        indicators=_NullIndicators(),
        tick_model=_OneTickPerBar(),
    )
    return interactor, _request(_bars(4), config=_config(**overrides))


#: **同一バー**に買い 2 本 → 反対の売り 1 本。並びを入れ替えると結果が変わる
#: （前から: 買い 2 本を建ててから売りが両方を reverse 決済 → 確定 2 本。
#:   後ろから: 売りを建ててから買いがそれを reverse 決済 → 確定 1 本）。
#: 走査順そのものを測るには、順序を変えると出力が変わる並びでなければならない。
_FILL_ORDERS = {0: [_market("buy"), _market("buy"), _market("sell")]}

#: 約定が起きるバーが 2 本になる並び（クォート導出のオーダーを 2 点で測るため）。
_FILL_ORDERS_TWO_BARS = {
    0: [_market("buy"), _market("buy"), _market("sell")],
    1: [_market("buy")],
}


class TestBothEnginesShareTheMarketFillStage:
    """成行約定（反対玉の reverse 決済 → 建玉 → 口座反映）の定義点が 1 つであること。"""

    def test_the_market_fill_stage_has_exactly_one_definition_point(self):
        assert callable(getattr(RunBacktestInteractor, "_fill_market_orders", None))

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_orders_are_applied_in_the_order_they_were_scanned(
        self, path, overrides, monkeypatch
    ):
        """走査順 ＝ 反映順（口座の保有列に載る順）であること。

        なぜ順序が仕様か: 保有列の並びは証拠金の按分解放（部分決済）と強制決済の走査に
        そのまま効く。走査順と反映順がずれると、同じ注文列から違う決済順が生まれ、
        確定トレードの並び（したがって sha256 指紋）が動く。
        """
        # Arrange: 約定の発行順を記録する。
        scanned: "list[str]" = []
        original = rb.fill_market_order

        def _spy(order, **kwargs):
            scanned.append(order.side)
            return original(order, **kwargs)

        monkeypatch.setattr(rb, "fill_market_order", _spy)
        interactor, request = _fill_scenario(overrides, _FILL_ORDERS)
        # Act
        result = interactor.execute(request)
        # Assert: 発行順・保有列の並び・確定トレードの並びが 1 本の順序で貫かれている。
        assert scanned == ["buy", "buy", "sell"]
        assert [t.side for t in result.trades] == ["buy", "buy"]
        assert [t.exit_reason for t in result.trades] == ["reverse", "reverse"]

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_each_fill_lands_in_the_account_before_the_next_order_is_scanned(
        self, path, overrides, monkeypatch
    ):
        """1 注文の反映が済んでから次の注文を走査すること（一括反映にしない）。

        なぜ交互でなければならないか: reverse 決済は「その時点の保有」を見る。注文を
        まとめて約定してから反映すると、2 本目の注文が 1 本目の建玉を見られず、
        反対玉の決済が起きるべきところで起きなくなる。
        """
        # Arrange: 各約定の直前に「口座がすでに何玉持っているか」を記録する。
        seen: "list[tuple[str, int]]" = []
        account_box: list = []

        class _Recording(_OrdersPerBar):
            def on_new_bar(self, bar_index, indicators, account):
                account_box.append(account)
                return super().on_new_bar(bar_index, indicators, account)

        original = rb.fill_market_order
        monkeypatch.setattr(
            rb,
            "fill_market_order",
            lambda order, **kw: (
                seen.append((order.side, len(account_box[-1].open_positions))),
                original(order, **kw),
            )[1],
        )
        interactor = RunBacktestInteractor(
            strategy=_Recording(_FILL_ORDERS),
            indicators=_NullIndicators(),
            tick_model=_OneTickPerBar(),
        )
        # Act
        interactor.execute(_request(_bars(4), config=_config(**overrides)))
        # Assert: 2 本目の買いは 1 本目の建玉を見ている（一括反映なら 0 のままになる）。
        #   売りは反対玉 2 本を reverse 決済した後なので、見える保有は 0。
        assert seen == [("buy", 0), ("buy", 1), ("sell", 0)]


class TestTheMarketFillDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。"""

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_no_entry_quote_is_derived_for_a_bar_that_has_no_orders(
        self, path, overrides, monkeypatch
    ):
        # Arrange: 建値クォートの導出発行を数える（注文が 1 本も無い run）。
        derived: "list[int]" = []
        original = rb.derive_quotes

        def _spy(bar, **kwargs):
            derived.append(1)
            return original(bar, **kwargs)

        monkeypatch.setattr(rb, "derive_quotes", _spy)
        # Act: 発注しない戦略で 32 バー走らせる。
        _interactor().execute(_request(_bars(32), config=_config(**overrides)))
        # Assert: 発行（建値クォート）− 使用（約定に使った回数 0）= 0。
        assert len(derived) - 0 == 0

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_entry_quote_is_derived_once_per_filling_bar_not_once_per_order(
        self, path, overrides, monkeypatch
    ):
        # Arrange
        derived: "list[int]" = []
        original = rb.derive_quotes
        monkeypatch.setattr(
            rb, "derive_quotes",
            lambda bar, **kw: (derived.append(1), original(bar, **kw))[1],
        )
        filled: "list[str]" = []
        original_fill = rb.fill_market_order
        monkeypatch.setattr(
            rb, "fill_market_order",
            lambda order, **kw: (filled.append(order.side), original_fill(order, **kw))[1],
        )
        interactor, request = _fill_scenario(overrides, _FILL_ORDERS)
        # Act
        interactor.execute(request)
        # Assert: 約定 3 本に対しクォート導出は 1 回（約定が起きたバーの数）。
        #   注文の数に比例しない＝1 バー 1 クォート。
        assert len(derived) - 1 == 0, (len(derived), filled)
        assert len(filled) - 3 == 0

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_quote_count_tracks_filling_bars_not_order_count(
        self, path, overrides, monkeypatch
    ):
        """約定バー 1 本 / 2 本の 2 点で「導出数 == 約定バー数」（オーダーの表明）。"""
        measured = {}
        # 素の実体は差し替える前に 1 度だけ捉える（2 周目に spy が spy を包むのを防ぐ）。
        original = rb.derive_quotes
        original_fill = rb.fill_market_order
        for filling_bars, orders_by_bar in ((1, _FILL_ORDERS), (2, _FILL_ORDERS_TWO_BARS)):
            derived: "list[int]" = []
            filled: "list[str]" = []
            monkeypatch.setattr(
                rb, "derive_quotes",
                lambda bar, **kw: (derived.append(1), original(bar, **kw))[1],
            )
            monkeypatch.setattr(
                rb, "fill_market_order",
                lambda order, **kw: (filled.append(order.side), original_fill(order, **kw))[1],
            )
            interactor, request = _fill_scenario(overrides, orders_by_bar)
            interactor.execute(request)
            measured[filling_bars] = (len(derived), len(filled))
        for filling_bars, (derived_count, filled_count) in measured.items():
            assert derived_count - filling_bars == 0, measured
        # 注文数は増えているのに導出は約定バー数しか増えない（注文数に非比例）。
        assert measured[2][1] - measured[1][1] == 1, measured


# ---- 4-6: 建玉変更（B2/B4）の単一化 ----

class _RecordingPositionManager:
    """建玉変更の呼ばれ方（粒度・参照価格・玉）だけを記録する（無変更を返す）。"""

    def __init__(self):
        self.calls: "list[tuple[str, str, float]]" = []

    def evaluate(self, *, ot, ref_price, granularity, account):
        self.calls.append((granularity, ot.position.side, ref_price))
        return None


def _held_positions_scenario(overrides, *, position_manager):
    """買い 2 玉を建てて数バー保有し続ける run（建玉変更が毎評価点で問われる）。"""
    interactor = RunBacktestInteractor(
        strategy=_OrdersPerBar({0: [_market("buy"), _market("buy")]}),
        indicators=_NullIndicators(),
        tick_model=_OneTickPerBar(),
        position_manager=position_manager,
    )
    return interactor, _request(_bars(6), config=_config(**overrides))


class TestBothEnginesShareThePositionDirectiveStage:
    """建玉変更の適用（粒度と参照価格の決め方）の定義点が 1 つであること。"""

    def test_the_position_directive_stage_has_exactly_one_definition_point(self):
        assert callable(getattr(RunBacktestInteractor, "_apply_position_directives", None))

    def test_the_bar_path_asks_at_bar_granularity_with_the_reached_extreme(self):
        """バー粒度の参照価格は「トレーリング方向の到達価格」（買い=high / 売り=low）。

        なぜ極値か: SL/TP の到達判定が high/low で touch を見るのと対称にするためである。
        close を参照にすると、同じバーで SL/TP は当たったのにトレーリングは動かない、
        という非対称が生まれる。
        """
        # Arrange
        pm = _RecordingPositionManager()
        interactor, request = _held_positions_scenario({}, position_manager=pm)
        bars = list(request.bars)
        # Act
        interactor.execute(request)
        # Assert: 粒度は "bar"、参照価格は買い玉なので当該バーの high。
        assert {granularity for granularity, _, _ in pm.calls} == {"bar"}
        asked_prices = {price for _, _, price in pm.calls}
        assert asked_prices <= {bar.high for bar in bars}

    def test_the_tick_path_asks_at_tick_granularity_with_the_exit_quote(self):
        """ティック粒度の参照価格は保有玉の決済価格（買い=Bid / 売り=Ask）。"""
        # Arrange
        pm = _RecordingPositionManager()
        interactor, request = _held_positions_scenario(
            {"tick_model": "real_ticks"}, position_manager=pm
        )
        bars = list(request.bars)
        # Act
        interactor.execute(request)
        # Assert: 粒度は "tick"、参照価格は各ティックの Bid（買い玉の決済価格）。
        assert {granularity for granularity, _, _ in pm.calls} == {"tick"}
        asked_prices = {price for _, _, price in pm.calls}
        assert asked_prices <= {bar.low for bar in bars}  # _OneTickPerBar の bid=bar.low

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_every_open_trade_is_offered_to_the_position_manager(self, path, overrides):
        # Arrange
        pm = _RecordingPositionManager()
        interactor, request = _held_positions_scenario(overrides, position_manager=pm)
        # Act
        interactor.execute(request)
        # Assert: 2 玉を保有しているので、各評価点で 2 回問われる（取りこぼしなし）。
        assert len(pm.calls) % 2 == 0
        assert len(pm.calls) > 0
        assert {side for _, side, _ in pm.calls} == {"buy"}

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_nothing_is_asked_when_no_position_manager_is_injected(self, path, overrides):
        """既定（未注入）では建玉変更の段を素通りすること（byte 等価の担保）。"""
        interactor, request = _held_positions_scenario(overrides, position_manager=None)
        result = interactor.execute(request)
        assert [t.exit_reason for t in result.trades] == []


class TestThePositionDirectiveStageDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。"""

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_exit_quote_is_not_resolved_once_per_open_trade(
        self, path, overrides, monkeypatch
    ):
        """決済価格の解決は評価点あたりで決まり、玉の数に比例しないこと。

        なぜ固定するか: 参照価格は玉のサイドだけで決まる（buy=Bid / sell=Ask）ので、
        玉ごとに引き直すのは同じ答えを人数分求める形（N+1）になる。玉が増えるほど
        捨てる計算が増えるが、出力は 1 ビットも変わらないため状態検証では落ちない。
        """
        # Arrange: 玉 1 / 玉 4 の 2 点で、玉あたりの決済価格解決の発行を測る。
        original = rb.close_price_for
        measured = {}
        for lot_count in (1, 4):
            resolved: "list[int]" = []
            monkeypatch.setattr(
                rb, "close_price_for",
                lambda side, **kw: (resolved.append(1), original(side, **kw))[1],
            )
            pm = _RecordingPositionManager()
            interactor = RunBacktestInteractor(
                strategy=_OrdersPerBar({0: [_market("buy")] * lot_count}),
                indicators=_NullIndicators(),
                tick_model=_OneTickPerBar(),
                position_manager=pm,
            )
            interactor.execute(_request(_bars(6), config=_config(**overrides)))
            measured[lot_count] = (len(resolved), len(pm.calls))
        # Assert: 建玉変更の問い合わせは玉数に比例して増える（玉ごとに 1 回・取りこぼし無し）。
        assert measured[4][1] - 4 * (measured[1][1]) == 0, measured
        # 一方、決済価格の解決は玉数に比例して増えない（評価点あたりで決まる）。
        assert measured[4][0] - measured[1][0] == 0, measured

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_manager_is_asked_exactly_once_per_open_trade_per_point(
        self, path, overrides
    ):
        # Arrange: 玉 2 / 玉 4 の 2 点。
        measured = {}
        for lot_count in (2, 4):
            pm = _RecordingPositionManager()
            interactor = RunBacktestInteractor(
                strategy=_OrdersPerBar({0: [_market("buy")] * lot_count}),
                indicators=_NullIndicators(),
                tick_model=_OneTickPerBar(),
                position_manager=pm,
            )
            interactor.execute(_request(_bars(6), config=_config(**overrides)))
            measured[lot_count] = len(pm.calls)
        # Assert: 発行（問い合わせ）− 使用（玉 × 評価点）= 0。玉を 2 倍にすれば 2 倍。
        assert measured[4] - 2 * measured[2] == 0, measured


# ---- 4-7: 評価点の決済（含み損益 → equity 記録 → stop-out 判定）の単一化 ----

class TestBothEnginesShareTheEvaluationPointSettlement:
    """1 評価点の口座再評価（I 段）の定義点が 1 つであること。"""

    def test_the_settlement_stage_has_exactly_one_definition_point(self):
        assert callable(getattr(RunBacktestInteractor, "_settle_evaluation_point", None))

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_one_equity_point_is_recorded_per_evaluation_point(self, path, overrides):
        # Arrange / Act: 発注しない run（評価点はバー数と一致する）。
        result = _interactor().execute(_request(_bars(12), config=_config(**overrides)))
        # Assert: 評価点 1 つにつき equity 系列へ 1 点（取りこぼしも重複も無い）。
        assert len(result.equity_curve) - 12 == 0

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_equity_series_is_unchanged_value_for_value(self, path, overrides):
        """含み玉を持つ run の equity 系列を値まで固定する（byte 一致の防波堤）。"""
        # Arrange: bar0 で買い建て、以降 6 バー保有し続ける。
        interactor = RunBacktestInteractor(
            strategy=_OrdersPerBar({0: [_market("buy")]}),
            indicators=_NullIndicators(),
            tick_model=_OneTickPerBar(),
        )
        request = _request(_bars(6), config=_config(**overrides))
        bars = list(request.bars)
        # Act
        result = interactor.execute(request)
        # Assert: 各点は「評価現値 − 建値」を deposit に足した値そのもの。
        #   （contract_size=1.0・volume=1.0・spread=0 なので含み損益 = 評価現値 − 建値）
        #   評価現値は経路で違う: バー評価は bar.close、ティック評価はティックの Bid。
        #   建値はどちらも足境界の bar open クォート（既定 "close" 基準＝bar0.close）。
        entry = bars[0].close
        mark = (lambda bar: bar.close) if path == "bar" else (lambda bar: bar.low)
        expected = [10_000.0 + (mark(bar) - entry) for bar in bars]
        assert len(result.equity_curve) == len(expected)
        for i, (got, want) in enumerate(zip(result.equity_curve, expected)):
            assert got == pytest.approx(want), (i, result.equity_curve, expected)

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_hedged_margin_rule_is_a_tick_granularity_rule(
        self, path, overrides, monkeypatch
    ):
        """両建ての証拠金相殺はティック評価にだけ効くこと（bar 経路は単純加算のまま）。

        現挙動の記録: 両建て相殺の設定を立てても bar 経路の stop-out 判定は実効証拠金を
        求めない。この非対称は設計上の意図ではなく現状の契約なので、段を束ねる改修で
        **どちらかに寄せてしまわない**ように固定する（寄せれば数値が動く）。
        """
        # Arrange: 相殺規則の呼び出しを数える。
        from simulator.domain.account import Account

        asked: "list[int]" = []
        original = Account.hedged_margin_level
        monkeypatch.setattr(
            Account,
            "hedged_margin_level",
            lambda self, **kw: (asked.append(1), original(self, **kw))[1],
        )
        interactor = RunBacktestInteractor(
            strategy=_OrdersPerBar({0: [_market("buy")]}),
            indicators=_NullIndicators(),
            tick_model=_OneTickPerBar(),
        )
        # Act
        interactor.execute(
            _request(_bars(6), config=_config(hedged_margin=True, **overrides))
        )
        # Assert: tick 経路だけが実効証拠金を求める。bar 経路は 1 度も求めない。
        assert (len(asked) > 0) is (path == "tick"), (path, len(asked))

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_settlement_reports_whether_the_run_was_halted(self, path, overrides):
        # Arrange: 割れて halt する run（close_and_halt）。
        interactor, request = _margin_breach_request(
            config_overrides={**overrides, "stop_out_action": "close_and_halt"},
            bars=_flat_then_crash_bars(),
        )
        # Act
        result = interactor.execute(request)
        # Assert: 強制決済されたうえで run は完走する（halt が呼出側へ伝わっている）。
        assert [t.exit_reason for t in result.trades] == ["stop_out"]
        assert len(result.equity_curve) > 0


class TestTheSettlementDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。"""

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_floating_pnl_is_updated_once_per_recorded_equity_point(
        self, path, overrides, monkeypatch
    ):
        # Arrange: 含み損益の更新発行を数える。
        from simulator.domain.account import Account

        updates: "list[int]" = []
        original = Account.update_floating_pnl_at
        monkeypatch.setattr(
            Account,
            "update_floating_pnl_at",
            lambda self, **kw: (updates.append(1), original(self, **kw))[1],
        )
        # Act
        result = _interactor().execute(_request(_bars(24), config=_config(**overrides)))
        # Assert: 発行（含み損益更新）− 使用（equity 系列に載った点）= 0。
        assert len(updates) - len(result.equity_curve) == 0

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_update_count_tracks_evaluation_points_not_open_trades(
        self, path, overrides, monkeypatch
    ):
        """玉 1 / 玉 4 の 2 点で、評価点あたりの更新発行が変わらないこと。"""
        from simulator.domain.account import Account

        original = Account.update_floating_pnl_at
        measured = {}
        for lot_count in (1, 4):
            updates: "list[int]" = []
            monkeypatch.setattr(
                Account,
                "update_floating_pnl_at",
                lambda self, **kw: (updates.append(1), original(self, **kw))[1],
            )
            interactor = RunBacktestInteractor(
                strategy=_OrdersPerBar({0: [_market("buy")] * lot_count}),
                indicators=_NullIndicators(),
                tick_model=_OneTickPerBar(),
            )
            result = interactor.execute(_request(_bars(8), config=_config(**overrides)))
            measured[lot_count] = (len(updates), len(result.equity_curve))
        # Assert: 玉を 4 倍にしても更新発行は増えない（玉数に非比例）。
        assert measured[4][0] - measured[1][0] == 0, measured
        for lot_count, (issued, used) in measured.items():
            assert issued - used == 0, measured

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_evaluation_quote_is_resolved_once_per_evaluation_point(
        self, path, overrides, monkeypatch
    ):
        """評価クォートを同じ引数で二度引かないこと（bar 経路の強制決済で起きていた形）。

        バー粒度の評価クォートはスケジュールが点を作るときに解決する（4-8 以降）ので、
        数えるのはそちらの発行である。
        """
        import simulator.usecase.bar_schedule as bar_schedule_mod

        resolved: "list[int]" = []
        original = bar_schedule_mod.resolve_eval_quote
        monkeypatch.setattr(
            bar_schedule_mod, "resolve_eval_quote",
            lambda bar, **kw: (resolved.append(1), original(bar, **kw))[1],
        )
        # Arrange / Act: 割れて強制決済する run（bar 経路はここで二度引いていた）。
        interactor, request = _margin_breach_request(
            config_overrides={**overrides, "stop_out_action": "close_and_halt"},
            bars=_flat_then_crash_bars(),
        )
        result = interactor.execute(request)
        # Assert: bar 経路は評価点ごとに 1 回だけ（tick 経路はティッククォートを使うので 0 回）。
        used = len(result.equity_curve) if path == "bar" else 0
        assert len(resolved) - used == 0, (len(resolved), used)


# ---- 4-8: 評価点による駆動（C1: 生んだ点 − 消費した点 = 0） ----

class _CountingSchedule:
    """生んだ点を数えるスケジュール（内側の実物へ委譲する）。"""

    def __init__(self, inner):
        self._inner = inner
        self.produced: "list[object]" = []

    @property
    def id(self):
        return self._inner.id

    def points(self, bar_index, bar, prev_close):
        for point in self._inner.points(bar_index, bar, prev_close):
            self.produced.append(point)
            yield point


class TestTheEngineIsDrivenByEvaluationPoints:
    """エンジンが評価点で駆動され、生んだ点をすべて消費すること。"""

    def test_the_point_evaluation_has_exactly_one_definition_point(self):
        assert callable(getattr(RunBacktestInteractor, "_evaluate_point", None))
        assert callable(getattr(RunBacktestInteractor, "_check_sltp_hits", None))

    def test_the_bar_path_takes_one_point_per_bar(self, monkeypatch):
        # Arrange: スケジュールを数える版に差し替える。
        from simulator.usecase.bar_schedule import BarSchedule

        boxes: "list[_CountingSchedule]" = []

        def _factory(**kwargs):
            box = _CountingSchedule(BarSchedule(**kwargs))
            boxes.append(box)
            return box

        monkeypatch.setattr(rb, "BarSchedule", _factory)
        # Act
        result = _interactor().execute(_request(_bars(16)))
        # Assert: 生んだ点 − 使った点（equity 系列に載った点）= 0。
        assert len(boxes) - 1 == 0  # スケジュールは run につき 1 つ
        assert len(boxes[0].produced) - len(result.equity_curve) == 0

    def test_the_point_count_tracks_bars_at_two_sizes(self, monkeypatch):
        """バー 50 本 / 200 本の 2 点で「点数 == バー数」（オーダーの表明）。"""
        from simulator.usecase.bar_schedule import BarSchedule

        measured = {}
        for bar_count in (50, 200):
            boxes: "list[_CountingSchedule]" = []
            monkeypatch.setattr(
                rb, "BarSchedule",
                lambda **kw: (boxes.append(_CountingSchedule(BarSchedule(**kw))), boxes[-1])[1],
            )
            result = _interactor().execute(_request(_bars(bar_count)))
            measured[bar_count] = (len(boxes[0].produced), len(result.equity_curve), bar_count)
        for bar_count, (produced, consumed, bars) in measured.items():
            assert produced - consumed == 0, measured
            assert produced - bars == 0, measured

    def test_every_produced_point_reaches_the_evaluation_procedure(self, monkeypatch):
        """点を作っておいて評価しない（作ってから捨てる）形になっていないこと。"""
        from simulator.usecase.bar_schedule import BarSchedule

        boxes: "list[_CountingSchedule]" = []
        monkeypatch.setattr(
            rb, "BarSchedule",
            lambda **kw: (boxes.append(_CountingSchedule(BarSchedule(**kw))), boxes[-1])[1],
        )
        evaluated: "list[object]" = []
        original = RunBacktestInteractor._evaluate_point
        monkeypatch.setattr(
            RunBacktestInteractor,
            "_evaluate_point",
            lambda self, state, point, open_trades, halted: (
                evaluated.append(point),
                original(self, state, point, open_trades, halted),
            )[1],
        )
        # Act
        _interactor().execute(_request(_bars(20)))
        # Assert: 発行 − 使用 = 0。順序も一致する（作った順に評価される）。
        assert len(boxes[0].produced) - len(evaluated) == 0
        assert boxes[0].produced == evaluated


# ---- 4-9: 点あたりの仕事量が両経路で同じであること（C2） ----

class _NTicksPerBar:
    """1 バーにつき n 本のティックを返す（点の数を経路間で変えて測るため）。"""

    def __init__(self, n):
        self._n = n

    def ticks_of(self, bar, prev_close):
        span = bar.high - bar.low
        out = []
        for i in range(self._n):
            price = bar.low + span * (i + 1) / (self._n + 1)
            out.append((price, price, price, bar.time))
        return out


def _measure_per_point(overrides, *, ticks_per_bar, bar_count, lot_count, monkeypatch):
    """1 run の「評価点あたりの発行数」を測る。

    数えるのは両経路が共有する 2 つの仕事:
      * SL/TP 到達判定（監視対象の玉ごと）
      * 含み損益の更新（点ごと）
    """
    from simulator.domain.account import Account

    hits: "list[int]" = []
    updates: "list[int]" = []
    original_hit = rb.check_sltp_hit
    original_update = Account.update_floating_pnl_at
    monkeypatch.setattr(
        rb, "check_sltp_hit",
        lambda position, **kw: (hits.append(1), original_hit(position, **kw))[1],
    )
    monkeypatch.setattr(
        Account, "update_floating_pnl_at",
        lambda self, **kw: (updates.append(1), original_update(self, **kw))[1],
    )
    interactor = RunBacktestInteractor(
        strategy=_OrdersPerBar({0: [_market("buy")] * lot_count}),
        indicators=_NullIndicators(),
        tick_model=_NTicksPerBar(ticks_per_bar),
    )
    result = interactor.execute(
        _request(_bars(bar_count), config=_config(**overrides))
    )
    points = len(result.equity_curve)
    return {
        "points": points,
        "hits_per_point": len(hits) / points,
        "updates_per_point": len(updates) / points,
    }


class TestBothPathsDoTheSameWorkPerPoint:
    """C2: 点あたりの発行比が bar 経路と tick 経路で同一であること。

    なぜ比で測るか: 2 つの経路は点の数が違う（バー粒度は 1 バー 1 点、ティック粒度は
    1 ティック 1 点）。総発行数を比べても点数の差しか見えない。**1 点あたり**に直すと、
    「同じ 1 点で同じだけの仕事をしているか」が測れる。片方の経路にだけ余分な仕事が
    残っていれば比がずれる。
    """

    def test_the_work_per_point_is_identical_across_paths(self, monkeypatch):
        # Arrange / Act: 同じシナリオを両経路で走らせる（ティック数は経路で違ってよい）。
        bar_side = _measure_per_point(
            {}, ticks_per_bar=1, bar_count=10, lot_count=2, monkeypatch=monkeypatch
        )
        tick_side = _measure_per_point(
            {"tick_model": "real_ticks"},
            ticks_per_bar=4, bar_count=10, lot_count=2, monkeypatch=monkeypatch,
        )
        # Assert: 点の数は違うが、点あたりの仕事量は同じ。
        assert tick_side["points"] > bar_side["points"]
        assert tick_side["hits_per_point"] - bar_side["hits_per_point"] == 0, (
            bar_side, tick_side
        )
        assert tick_side["updates_per_point"] - bar_side["updates_per_point"] == 0, (
            bar_side, tick_side
        )

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_work_per_point_does_not_grow_with_the_number_of_ticks(
        self, path, overrides, monkeypatch
    ):
        """ティック 2 本 / 16 本の 2 点で、点あたりの発行が変わらないこと。"""
        measured = {}
        for ticks_per_bar in (2, 16):
            measured[ticks_per_bar] = _measure_per_point(
                overrides, ticks_per_bar=ticks_per_bar, bar_count=8, lot_count=2,
                monkeypatch=monkeypatch,
            )
        assert (
            measured[16]["hits_per_point"] - measured[2]["hits_per_point"] == 0
        ), measured
        assert (
            measured[16]["updates_per_point"] - measured[2]["updates_per_point"] == 0
        ), measured

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_evaluation_work_per_point_does_not_grow_with_the_number_of_bars(
        self, path, overrides, monkeypatch
    ):
        """バー 50 本 / 200 本の 2 点で、点あたりの発行が入力量に依らないこと。

        到達判定の発行は「監視対象の点 × 監視対象の玉」ちょうどである。建てたバーの点は
        監視外（fill_delay=次tick）なので、そのぶんを引いた数と突き合わせる。比で見ると
        建て足の免除がバー数によって薄まり、量ではなく端数を見てしまう。
        """
        lot_count, ticks_per_bar = 1, 2
        measured = {}
        for bar_count in (50, 200):
            measured[bar_count] = _measure_per_point(
                overrides, ticks_per_bar=ticks_per_bar, bar_count=bar_count,
                lot_count=lot_count, monkeypatch=monkeypatch,
            )
        for bar_count, m in measured.items():
            # 含み損益の更新は点あたりちょうど 1（点数に比例・それ以上でも以下でもない）。
            assert m["updates_per_point"] - 1 == 0, measured
            # 到達判定は「監視対象の点 × 玉」ちょうど（発行 − 使用 = 0）。
            monitored_points = m["points"] - (m["points"] / bar_count)
            issued = m["hits_per_point"] * m["points"]
            assert issued - lot_count * monitored_points == 0, (bar_count, measured)


# ---- 4-10: 1 本のエンジン（C3: 2 点オーダー 3 種） ----

class TestThereIsOnlyOneEngine:
    """実行経路が 1 本であること。"""

    def test_the_engine_body_has_exactly_one_definition_point(self):
        assert callable(getattr(RunBacktestInteractor, "_run", None))

    def test_there_is_no_separate_every_tick_engine_any_more(self):
        """粒度ごとの別エンジンが残っていないこと。"""
        assert getattr(RunBacktestInteractor, "_execute_every_tick", None) is None

    def test_the_entry_point_only_picks_a_schedule_and_runs(self):
        """入口はスケジュールを選んで本体へ渡すだけであること（分岐を持たない）。"""
        import ast
        import inspect

        source = inspect.getsource(RunBacktestInteractor.execute)
        tree = ast.parse(source.lstrip())
        branches = [
            n for n in ast.walk(tree) if isinstance(n, (ast.If, ast.For, ast.While))
        ]
        assert branches == []

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_schedule_matches_the_granularity_the_run_asked_for(self, path, overrides):
        from simulator.usecase.run_features import RunFeatures

        request = _request(_bars(4), config=_config(**overrides))
        schedule = _interactor()._make_schedule(request, RunFeatures.of(request.config))
        assert schedule.id == path

    def test_a_pending_lifecycle_run_takes_tick_granularity(self):
        from simulator.usecase.run_features import RunFeatures

        request = _request(_bars(4), config=_config(pending_lifecycle=True))
        schedule = _interactor()._make_schedule(request, RunFeatures.of(request.config))
        assert schedule.id == "tick"


class TestTheEngineHoldsItsOrderAcrossSizes:
    """C3: 入力量を変えた 2 点 3 種で、点あたりの発行が変わらないこと。"""

    @pytest.mark.parametrize("path,overrides", _BOTH_PATHS, ids=lambda v: v if isinstance(v, str) else "")
    def test_the_work_per_point_holds_across_bars_ticks_and_lots(
        self, path, overrides, monkeypatch
    ):
        """バー 50/200・ティック 2/16・玉 1/4 の 3 軸で点あたりの発行を測る。

        3 軸すべてで「点あたりの含み損益更新が 1 のまま」であることが、評価の仕事が
        点の数だけで決まる（バー数にも、ティック数にも、玉数にも比例しない）ことの表明である。
        """
        axes = {
            "bars": ((50, 200), lambda v: dict(bar_count=v, ticks_per_bar=2, lot_count=1)),
            "ticks": ((2, 16), lambda v: dict(bar_count=8, ticks_per_bar=v, lot_count=1)),
            "lots": ((1, 4), lambda v: dict(bar_count=8, ticks_per_bar=2, lot_count=v)),
        }
        for axis, (values, make) in axes.items():
            measured = {
                v: _measure_per_point(overrides, monkeypatch=monkeypatch, **make(v))
                for v in values
            }
            low, high = values
            assert (
                measured[high]["updates_per_point"] - measured[low]["updates_per_point"]
            ) == 0, (axis, measured)
            assert measured[low]["updates_per_point"] - 1 == 0, (axis, measured)


# ---- 4-11: 粒度の選択規則と加法注入（O-1） ----

class TestTheEngineHoldsNoGranularityCondition:
    """粒度を決める条件が実行経路に書かれていないこと。"""

    def test_the_engine_holds_no_tick_model_name_literal(self):
        """実行経路に粒度を決める文字列が 1 つも無いこと（AST）。

        規則の所在は選択規則の表 1 箇所である。実行経路にリテラルが残っていると、
        条件を増やすときに実行経路を開くことになり、しかも表と実行経路が食い違いうる。
        """
        import ast

        offenders = [
            (node.lineno, node.value)
            for node in ast.walk(_run_backtest_tree())
            if isinstance(node, ast.Constant) and node.value == "real_ticks"
        ]
        assert offenders == [], f"粒度を決める文字列が実行経路に残っている: {offenders}"

    def test_taking_a_row_out_of_the_trigger_table_changes_the_granularity(
        self, monkeypatch
    ):
        """**負の対照**: 表が実際に粒度を決めていること。

        表から実ティックの行を外すと、実ティックの run がバー粒度で走るようになる
        （＝表が飾りではなく判定そのものであることの実証）。
        """
        import simulator.usecase.schedule_selection as selection
        from simulator.usecase.run_features import RunFeatures

        request = _request(_bars(4), config=_config(tick_model="real_ticks"))
        features = RunFeatures.of(request.config)
        # 表が効いているとき
        assert _interactor()._make_schedule(request, features).id == "tick"
        # 行を外すと効かなくなる
        monkeypatch.setattr(
            selection,
            "TICK_GRANULARITY_TRIGGERS",
            tuple(r for r in selection.TICK_GRANULARITY_TRIGGERS if r[0] != "tick_model"),
        )
        assert _interactor()._make_schedule(request, features).id == "bar"


class TestTheScheduleCanBeSuppliedFromOutside:
    """スケジュールの加法注入（既定 None＝従来どおり run ごとに組む）。"""

    def test_the_constructor_takes_a_schedule_with_a_default_of_none(self):
        import inspect

        parameter = inspect.signature(RunBacktestInteractor.__init__).parameters["schedule"]
        assert parameter.default is None
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_a_run_without_an_injected_schedule_builds_its_own(self):
        # 既定の 44 構築点はこの経路を通る（1 行も変えていない）。
        assert _interactor()._schedule is None
        result = _interactor().execute(_request(_bars(6)))
        assert len(result.equity_curve) - 6 == 0

    def test_an_injected_schedule_is_used_verbatim(self):
        # Arrange: 生んだ点を数えるスケジュールを外から渡す。
        from simulator.usecase.bar_schedule import BarSchedule

        injected = _CountingSchedule(
            BarSchedule(floating_pnl_basis="close", point_size=0.00001)
        )
        interactor = RunBacktestInteractor(
            strategy=_NullStrategy(),
            indicators=_NullIndicators(),
            tick_model=_OneTickPerBar(),
            schedule=injected,
        )
        # Act
        result = interactor.execute(_request(_bars(9)))
        # Assert: 注入した実体が run を駆動した（自前で組み直していない）。
        assert len(injected.produced) - len(result.equity_curve) == 0
        assert len(injected.produced) - 9 == 0

    def test_an_injected_schedule_overrides_the_granularity_the_config_asks_for(self):
        """注入は選択規則より優先される（呼出側が粒度を決められる＝拡張点）。"""
        from simulator.usecase.bar_schedule import BarSchedule

        interactor = RunBacktestInteractor(
            strategy=_NullStrategy(),
            indicators=_NullIndicators(),
            tick_model=_OneTickPerBar(),
            schedule=BarSchedule(floating_pnl_basis="close", point_size=0.00001),
        )
        # config は実ティックを求めるが、注入されたバー粒度で走る。
        result = interactor.execute(
            _request(_bars(6), config=_config(tick_model="real_ticks"))
        )
        assert len(result.equity_curve) - 6 == 0
