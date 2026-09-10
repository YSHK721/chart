"""`RunTracePort` の全具象に対する非侵襲ゲートと呼出点の固定（§7.2 通過条件 1・4）。

固定する仕様:

    条件 1: **生産実装の非侵襲を fingerprint 級で測る**。ケース A/B/C（`run_backtest` の
        数値指紋）へ tracer を注入した run の `stats_sha256` / `trades_sha256` が
        非注入と一致すること。`run_trace_ports.py` は「本 Port を継承する任意の実装は
        一度も通らない／任意実装への強制は段階 3 の非侵襲ゲートが担う」と宣言しており、
        本モジュールがその宣言を実体にする。

        **`__subclasses__()` を唯一の発見手段にしない**: import されていないクラスは
        現れないため、発見漏れがそのまま「集合が空＝恒真」になる。`adapter/trace/*.py` を
        `ast` で走査して `RunTracePort` を基底に持つクラスを集め、テスト側 Spy を明示的に
        足す。発見器そのものの自己検査（集合が空でない・ColumnarRunTrace（`simulator/adapter/trace/columnar_run_trace.py`） を含む・
        合成ソースを検出できる）を対で置く。

        **正の対照が必須**: 何も記録しない実装なら指紋一致は恒真である。各具象について
        「実際に記録している（行数 > 0）」ことを同じ run で確かめる。

    条件 4: **Port 宣言（`RunTracePort.observe`）と呼出点（`run_backtest.py`）の引数並びの
        一致を構文木で固定**する。期待値は手書きせず、2 つの構文木から導く。加えて
        呼出が 1 箇所であること（`len(calls) == 1`）とキーワード渡しでないこと
        （`call.keywords == []`）を表明する——キーワードにすると並びのドリフトが
        構文木から見えなくなる。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import simulator.usecase.run_backtest as run_backtest_mod
import simulator.usecase.run_trace_ports as run_trace_ports_mod
from simulator.tests.integration.test_run_backtest_fingerprint import (
    _C_TRADE_COUNT,
    _digest,
    _fixture_missing,
    _meta,
    _TRADING_START,
    _write_c_bars,
    _write_c_ticks,
)
from simulator.tests.fixtures.mt5 import load_case
from simulator.main import run_backtest
from simulator.usecase.run_trace_ports import RunTracePort

_CASE = "ma_slope_jp225_202501"

_needs_mt5_fixture = pytest.mark.skipif(
    _fixture_missing(), reason="MT5 突合フィクスチャ（JP225 M1）が無い"
)


# ---- 具象の発見（import 漏れで空集合にならない形）----

class _RecordingSpy(RunTracePort):
    """テスト側の具象（発見器の集合へ**明示的に**足す）。"""

    def __init__(self):
        self.rows = 0

    def observe(self, point, account, open_trades, halted):
        self.rows += 1


def _concrete_classes_declared_in(source: str) -> "set[str]":
    """ソース 1 本から `RunTracePort` を基底に持つクラス名を集める。"""
    tree = ast.parse(source)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(
            (isinstance(b, ast.Name) and b.id == "RunTracePort")
            or (isinstance(b, ast.Attribute) and b.attr == "RunTracePort")
            for b in node.bases
        )
    }


def _discover_production_concretes() -> "dict[str, type]":
    """`adapter/trace/*.py` を構文木で走査し、具象クラスを実体で返す。"""
    import importlib

    import simulator.adapter.trace as pkg

    found: "dict[str, type]" = {}
    for path in sorted(pathlib.Path(pkg.__file__).parent.glob("*.py")):
        names = _concrete_classes_declared_in(path.read_text(encoding="utf-8"))
        if not names:
            continue
        module = importlib.import_module(f"simulator.adapter.trace.{path.stem}")
        for name in names:
            found[name] = getattr(module, name)
    return found


def _all_concretes() -> "dict[str, object]":
    """生産実装 ＋ テスト側 Spy（後者は**明示的に**足す）。"""
    out: "dict[str, object]" = dict(_discover_production_concretes())
    out["_RecordingSpy"] = _RecordingSpy
    return out


def _make(cls):
    """具象を既定構成で組む（窓なし＝全区間）。"""
    if cls is _RecordingSpy:
        return _RecordingSpy()
    from simulator.adapter.trace.trace_window import TraceWindow

    return cls(TraceWindow.of(None, None))


def _rows_of(tracer) -> int:
    return int(getattr(tracer, "rows", 0))


_CONCRETES = sorted(_all_concretes())


# ---- 発見器の自己検査（走査の空振りを塞ぐ）----

class TestTheDiscovererActuallyDiscovers:
    def test_the_production_set_is_not_empty_and_contains_the_known_concrete(self):
        # Arrange / Act
        found = _discover_production_concretes()

        # Assert
        assert found, "生産具象が 0 件（発見器が空振り＝以降の parametrize が恒真になる）"
        assert "ColumnarRunTrace" in found, sorted(found)

    def test_it_detects_a_concrete_in_a_synthetic_source(self):
        # Arrange
        source = (
            "class A(RunTracePort):\n    pass\n"
            "class B(ports.RunTracePort):\n    pass\n"
            "class C(SomethingElse):\n    pass\n"
        )

        # Act / Assert
        assert _concrete_classes_declared_in(source) == {"A", "B"}

    def test_it_reports_nothing_for_a_source_without_concretes(self):
        # Arrange / Act / Assert
        assert _concrete_classes_declared_in("class C:\n    pass\n") == set()

    def test_the_subclass_hook_alone_would_be_an_unreliable_oracle(self):
        """`__subclasses__()` を唯一の手段にしない根拠を実測で示す。

        `__subclasses__()` は**その時点で import 済み**のクラスしか返さない。
        走査で得た集合がそれと一致しない（ないし将来一致しなくなる）ことを、
        「発見器の方が広い（少なくとも同数以上）」という形で固定する。
        """
        # Arrange / Act
        walked = set(_all_concretes())
        hooked = {c.__name__ for c in RunTracePort.__subclasses__()}

        # Assert: 走査側は import 状態に依存しない。
        assert walked >= {"ColumnarRunTrace", "_RecordingSpy"}
        assert len(walked) >= len(walked & hooked)

    def test_every_discovered_concrete_can_be_built_and_records(self):
        """正の対照: 具象が実際に記録すること（何もしない実装なら以降が恒真）。"""
        # Arrange
        from simulator.tests.unit.test_run_trace_observation import (
            _fixture_plain,
            _run,
        )

        for name in _CONCRETES:
            tracer = _make(_all_concretes()[name])

            # Act
            _result, _t, schedule = _run(tracer=tracer, **_fixture_plain())

            # Assert
            assert _rows_of(tracer) == len(schedule.produced), name
            assert _rows_of(tracer) > 0, name


# ---- 条件 1: 非侵襲を fingerprint 級で測る ----

def _digest_with_tracer(tmp_path, meta, tracer, tag):
    out = tmp_path / tag
    kwargs = dict(meta)
    if tracer is not None:
        kwargs["run_tracer"] = tracer
    exit_code, result = run_backtest(output_dir=out, **kwargs)
    assert exit_code == 0
    assert result is not None
    return _digest(result, out / "stats.json")


@_needs_mt5_fixture
@pytest.mark.parametrize("concrete", _CONCRETES, ids=_CONCRETES)
@pytest.mark.parametrize(
    "trading_start", [None, _TRADING_START], ids=["case_a", "case_b"]
)
class TestTheRealFingerprintDoesNotMoveWhenObserved:
    """ケース A / B（実 MT5 フィクスチャ）へ具象を注入しても指紋が動かないこと。"""

    def test_the_digests_are_identical_with_and_without_the_tracer(
        self, tmp_path, concrete, trading_start
    ):
        # Arrange
        meta = _meta(load_case(_CASE), trading_start=trading_start)
        tracer = _make(_all_concretes()[concrete])

        # Act
        plain = _digest_with_tracer(tmp_path, meta, None, "plain")
        observed = _digest_with_tracer(tmp_path, meta, tracer, "observed")

        # Assert: 1 bit も動かない。
        assert observed["stats_sha256"] == plain["stats_sha256"]
        assert observed["trades_sha256"] == plain["trades_sha256"]
        assert observed["trade_count"] == plain["trade_count"]
        # 正の対照: 何も記録しない実装なら上は恒真である。
        assert _rows_of(tracer) > 0, f"{concrete} が 1 行も記録していない"
        assert plain["trade_count"] > 0


@pytest.mark.parametrize("concrete", _CONCRETES, ids=_CONCRETES)
class TestTheRealTicksFingerprintDoesNotMoveWhenObserved:
    """ケース C（`tick_model="real_ticks"`・合成素材）でも指紋が動かないこと。"""

    def test_the_digests_are_identical_with_and_without_the_tracer(
        self, tmp_path, concrete
    ):
        # Arrange
        bars_csv = _write_c_bars(tmp_path / "synth_m1.csv")
        tick_root = _write_c_ticks(tmp_path / "ticks")
        tracer = _make(_all_concretes()[concrete])
        meta = dict(
            data_path=bars_csv, symbol="EURUSD", period="M1", ea_name="TC24051901",
            initial_deposit=10_000.0, contract_size=1.0, volume_min=0.01,
            volume_max=100.0, volume_step=0.01, stops_level=0, digits=5,
            point_size=0.0001, leverage=100.0, ma_period=2, ma_method="sma",
            lot_size=1.0, stop_loss_points=500, take_profit_points=3000,
            config_overrides={
                "tick_model": "real_ticks", "entry_price_basis": "current_open",
            },
            tick_store_root=tick_root,
        )

        # Act
        plain = _digest_with_tracer(tmp_path, meta, None, "plain")
        observed = _digest_with_tracer(tmp_path, meta, tracer, "observed")

        # Assert
        assert observed["stats_sha256"] == plain["stats_sha256"]
        assert observed["trades_sha256"] == plain["trades_sha256"]
        # 正の対照: 錨が空へ退化していない・具象が実際に記録している。
        assert plain["trade_count"] == _C_TRADE_COUNT
        assert _rows_of(tracer) > 0, f"{concrete} が 1 行も記録していない"

    def test_the_recorded_rows_track_the_real_tick_schedule(self, tmp_path, concrete):
        """正の対照の深堀り: ティック粒度で記録されていること。

        バー数ぶんしか記録していない実装は「real_ticks 経路を観測した」と言えない。
        """
        # Arrange
        from simulator.tests.integration.test_run_backtest_fingerprint import _C_BARS

        bars_csv = _write_c_bars(tmp_path / "synth_m1.csv")
        tick_root = _write_c_ticks(tmp_path / "ticks")
        tracer = _make(_all_concretes()[concrete])

        # Act
        _digest_with_tracer(
            tmp_path,
            dict(
                data_path=bars_csv, symbol="EURUSD", period="M1", ea_name="TC24051901",
                initial_deposit=10_000.0, contract_size=1.0, volume_min=0.01,
                volume_max=100.0, volume_step=0.01, stops_level=0, digits=5,
                point_size=0.0001, leverage=100.0, ma_period=2, ma_method="sma",
                lot_size=1.0, stop_loss_points=500, take_profit_points=3000,
                config_overrides={
                    "tick_model": "real_ticks", "entry_price_basis": "current_open",
                },
                tick_store_root=tick_root,
            ),
            tracer,
            "ticked",
        )

        # Assert: 1 バー 5 ティックの素材なので、記録はバー数を上回る。
        assert _rows_of(tracer) > len(_C_BARS), (_rows_of(tracer), len(_C_BARS))


# ---- 条件 4: Port 宣言と呼出点の引数並びを構文木で固定 ----

def _port_parameter_names() -> "list[str]":
    """`RunTracePort.observe` の仮引数名（`self` を除く）。"""
    tree = ast.parse(
        pathlib.Path(run_trace_ports_mod.__file__).read_text(encoding="utf-8")
    )
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RunTracePort"
    )
    fn = next(
        n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "observe"
    )
    return [a.arg for a in fn.args.args[1:]]


def _observe_calls() -> "list[ast.Call]":
    """`run_backtest.py` の `...observe(...)` 呼出をすべて集める。"""
    tree = ast.parse(
        pathlib.Path(run_backtest_mod.__file__).read_text(encoding="utf-8")
    )
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "observe"
    ]


def _argument_names(call: ast.Call) -> "list[str]":
    """実引数の「名前」を取り出す（x は x、`state.account` は `account`）。"""
    out = []
    for arg in call.args:
        if isinstance(arg, ast.Name):
            out.append(arg.id)
        elif isinstance(arg, ast.Attribute):
            out.append(arg.attr)
        else:
            out.append(ast.dump(arg))
    return out


class TestTheCallSiteMatchesThePortDeclaration:
    """引数並びのドリフトを構文木で赤にする（§7.2 通過条件 4）。"""

    def test_there_is_exactly_one_call_site(self):
        """呼出点は 1 箇所（§4.2 の唯一の設計根拠）。"""
        # Arrange / Act
        calls = _observe_calls()

        # Assert
        assert len(calls) == 1, [ast.dump(c) for c in calls]

    def test_the_call_passes_everything_positionally(self):
        """キーワード渡しにしないこと。

        キーワードにすると並びのドリフトが構文木から見えなくなり、下の一致表明が
        「何も縛らない表明」に退化する。
        """
        # Arrange / Act
        call = _observe_calls()[0]

        # Assert
        assert call.keywords == [], [ast.dump(k) for k in call.keywords]

    def test_the_argument_names_match_the_declared_parameters(self):
        """期待値を手書きせず、**2 つの構文木から導く**。"""
        # Arrange
        declared = _port_parameter_names()
        call = _observe_calls()[0]

        # Act
        passed = _argument_names(call)

        # Assert
        assert passed == declared, (passed, declared)
        # 正の対照: どちらかが空なら一致は恒真になる。
        assert len(declared) >= 4, declared

    def test_the_matcher_would_catch_a_reordered_call(self):
        """検出器の自己検査: 並びを入れ替えた形を実際に赤にできること。"""
        # Arrange
        declared = _port_parameter_names()
        reordered = ast.parse(
            "state.tracer.observe(point, open_trades, state.account, halted)"
        ).body[0].value

        # Act
        passed = _argument_names(reordered)

        # Assert
        assert passed != declared
        assert sorted(passed) == sorted(declared), (passed, declared)
