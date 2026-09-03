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
