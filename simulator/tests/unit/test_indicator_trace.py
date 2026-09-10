"""IndicatorTrace: 何を残すか（足粒度）（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.5.2）。

固定する仕様:

    1. 行粒度は**足**である（点ではない）。指標は足単位でしか動かないので、点ごとに写せば
       同じ値をティック本数ぶん重複させるだけになる（§6.2 の複製禁止）。
    2. 鍵は bar_index。記録するのは ColumnarRunTrace（`simulator/adapter/trace/columnar_run_trace.py`） が**実際に記録した** bar_index
       の異なりだけである（窓の外の足は 1 行も出ない）。
    3. 値は「**エンジンが実際に読んだ値**」＝戦略と同じ引き方（`series.iloc[bar_index]`）。
       トレースは run の事実の記録なので、別の引き方に置き換えない。
    4. 系列名の列挙は `IndicatorSeriesNamesPort.names()`（§6.3）。列挙できない供給は
       **明示エラー**（空で黙って続行しない＝「指標が無い run」に見せない・§7）。

計算量（プロジェクト絶対命令 2026-08-28）:
    **行数 − 記録した bar_index の異なり数 = 0**。加えて、系列の取得（`registry.get`）を
    行ごとに発行しない——`発行した get − 系列本数 = 0` を Spy で表明する。行ごとに引くと
    出力は同じまま発行だけが行数倍になる（「作ってから捨てる」形・状態検証では原理的に
    落ちない）。オーダーは行数の異なる 2 点で固定する（回数そのものは焼き込まない）。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from simulator.adapter.trace.indicator_trace import BAR_INDEX_COLUMN, IndicatorTrace
from simulator.domain.exceptions import ConfigError


class _Series:
    """位置参照だけを持つ系列の代役（`pandas.Series.iloc` と同じ引き方）。"""

    class _Iloc:
        def __init__(self, values):
            self._values = values

        def __getitem__(self, index):
            return self._values[index]

    def __init__(self, values):
        self._values = list(values)
        self.iloc = self._Iloc(self._values)


class _Registry:
    """`names()` / `get(name)` を持つ registry の Spy（発行回数を数える）。"""

    def __init__(self, series):
        self._series = dict(series)
        self.get_calls = 0
        self.names_calls = 0

    def names(self):
        self.names_calls += 1
        return tuple(self._series)

    def get(self, name):
        self.get_calls += 1
        return self._series[name]


class _NoNamesRegistry:
    """列挙契約を持たない供給（IndicatorPort（`simulator/usecase/ports.py`） だけを満たす古い実装）。"""

    def get(self, name):
        raise AssertionError("列挙できない供給から値を引いてはならない")


def _registry(**series):
    return _Registry({name: _Series(values) for name, values in series.items()})


# ---- 1・2・3: 足粒度の行と値 ----

class TestTheIndicatorTraceIsKeyedByBarIndex:
    """1 足 1 行・記録された足だけ・戦略と同じ引き方。"""

    def test_it_emits_one_row_per_distinct_recorded_bar_index(self):
        # Arrange: 同じ足が複数点ぶん記録されている（1 バー複数ティック）。
        registry = _registry(ma=[10.0, 11.0, 12.0, 13.0])
        recorded = [1, 1, 1, 2, 2, 3]

        # Act
        columns = IndicatorTrace(registry).columns_for(recorded)

        # Assert: 足の異なりだけが行になる（点数ぶんには増えない）。
        assert columns[BAR_INDEX_COLUMN] == [1, 2, 3]
        assert columns["ma"] == [11.0, 12.0, 13.0]

    def test_the_rows_follow_the_order_the_bars_were_recorded_in(self):
        """並びは**記録順**（最初に現れた順）であること。

        並べ替えると trace_points.parquet の並びと突き合わせる側が別の順序規則を
        知る必要が生まれる。run の事実の記録なので run の順序をそのまま残す。
        """
        # Arrange
        registry = _registry(ma=[0.0, 1.0, 2.0, 3.0, 4.0])
        recorded = [4, 4, 2, 2, 3]

        # Act
        columns = IndicatorTrace(registry).columns_for(recorded)

        # Assert
        assert columns[BAR_INDEX_COLUMN] == [4, 2, 3]
        assert columns["ma"] == [4.0, 2.0, 3.0]

    def test_every_registered_series_becomes_a_column_in_registration_order(self):
        # Arrange: 辞書順とは異なる登録順にする。
        registry = _registry(ma=[1.0, 2.0], close=[3.0, 4.0], adx=[5.0, 6.0])

        # Act
        columns = IndicatorTrace(registry).columns_for([0, 1])

        # Assert: 先頭が鍵、続いて登録順の系列。
        assert list(columns) == [BAR_INDEX_COLUMN, "ma", "close", "adx"]
        assert columns["close"] == [3.0, 4.0]

    def test_no_recorded_bar_means_no_row(self):
        """窓の外しか無い run（記録 0 件）では 1 行も出ないこと（境界値）。"""
        # Arrange
        registry = _registry(ma=[1.0, 2.0])

        # Act
        columns = IndicatorTrace(registry).columns_for([])

        # Assert: 列の形は保ちつつ行は 0（列そのものが消えると読み手が形を失う）。
        assert list(columns) == [BAR_INDEX_COLUMN, "ma"]
        assert all(v == [] for v in columns.values())

    def test_a_registry_without_series_still_emits_the_key_column(self):
        """系列 0 本の供給（NullIndicatorRegistry（`simulator/adapter/indicator/null_registry.py`））でも足の並びは残ること。

        「系列を 1 本も持たない」は正当な run の姿であり、失敗ではない（§7 で明示
        エラーにすると定めたのは「列挙**できない**供給」であって空の供給ではない）。
        """
        # Arrange
        registry = _Registry({})

        # Act
        columns = IndicatorTrace(registry).columns_for([2, 2, 5])

        # Assert
        assert columns == {BAR_INDEX_COLUMN: [2, 5]}


class TestTheValuesAreTheOnesTheEngineActuallyRead:
    """戦略と同じ位置参照（`series.iloc[bar_index]`）で引くこと。"""

    def test_the_value_is_taken_by_positional_lookup(self):
        # Arrange: 位置と値がずれていれば取り違えが見える形にする。
        registry = _registry(ma=[100.0, 200.0, 300.0])

        # Act
        columns = IndicatorTrace(registry).columns_for([2, 0])

        # Assert
        assert columns["ma"] == [300.0, 100.0]

    def test_the_module_uses_iloc_and_not_a_second_lookup_rule(self):
        """構文木に `.iloc` 以外の引き方（loc / at / `values[...]`）が無いこと。"""
        # Arrange
        import simulator.adapter.trace.indicator_trace as mod

        # Act
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }

        # Assert
        assert "iloc" in attributes, attributes
        assert attributes & {"loc", "at", "iat"} == set(), attributes


class TestAnUnenumerableSupplyFailsLoudly:
    """§7: 「指標系列を列挙できない供給」は明示エラー。"""

    def test_a_registry_without_names_raises_instead_of_returning_nothing(self):
        # Arrange
        trace = IndicatorTrace(_NoNamesRegistry())

        # Act / Assert: 空で黙って続行しない（「指標が無い run」に見せない）。
        with pytest.raises(ConfigError):
            trace.columns_for([0, 1])

    def test_the_failure_names_the_supply(self):
        # Arrange
        trace = IndicatorTrace(_NoNamesRegistry())

        # Act
        with pytest.raises(ConfigError) as excinfo:
            trace.columns_for([0])

        # Assert: 何が列挙できなかったのかが運用者へ届く。
        # 何が列挙できなかったのかは context の registry キーが運ぶ（実装の契約）。
        # 「文言か context のどちらかに型名が在る」では、context を空にする実装が
        # 文言だけで通ってしまい、機械可読な情報が失われたことを検出できない。
        assert excinfo.value.context["registry"] == "_NoNamesRegistry"


# ---- 計算量 ----

class TestTheIndicatorTraceIssuesNoThrowawayWork:
    """行数 − 足の異なり数 = 0／系列の取得は 1 本 1 回。"""

    def test_the_rows_minus_the_distinct_recorded_bars_is_zero(self):
        # Arrange
        registry = _registry(ma=list(range(50)), close=list(range(50)))
        recorded = [i // 3 for i in range(60)]  # 1 足あたり 3 点

        # Act
        columns = IndicatorTrace(registry).columns_for(recorded)

        # Assert: 発行（行）− 使用（足の異なり）= 0。
        distinct = len(set(recorded))
        assert len(columns[BAR_INDEX_COLUMN]) - distinct == 0
        assert {len(v) for v in columns.values()} == {distinct}
        # 正の対照: 0 件なら差 0 は恒真になる。点数より行数が少ないことも確かめる
        # （足の異なりに畳めていないと点数ぶんの行が出る）。
        assert distinct > 0, "足が 0 件（検定が何も測っていない）"
        assert distinct < len(recorded), (distinct, len(recorded))

    def test_the_series_are_fetched_once_each_and_not_once_per_row(self):
        """`発行した get − 系列本数 = 0`。

        行ごとに `registry.get(name)` を引くと、出力は同じまま発行だけが行数倍になる
        （作ってから捨てる形）。回数そのものは期待値へ焼き込まず、**系列本数との差**
        で表明する。
        """
        # Arrange
        registry = _registry(ma=list(range(40)), close=list(range(40)))
        recorded = list(range(30))

        # Act
        columns = IndicatorTrace(registry).columns_for(recorded)

        # Assert
        series_count = len(registry.names())
        assert registry.get_calls - series_count == 0, (registry.get_calls, series_count)
        # 正の対照: 行が 0 件なら上は恒真になりうる。
        assert len(columns[BAR_INDEX_COLUMN]) > 1, columns[BAR_INDEX_COLUMN]

    def test_the_fetches_do_not_grow_with_the_number_of_rows(self):
        """オーダー: 行数を変えた 2 点で系列取得の発行が変わらないこと。"""
        # Arrange / Act
        measured = {}
        for rows in (5, 40):
            registry = _registry(ma=list(range(50)), close=list(range(50)))
            IndicatorTrace(registry).columns_for(list(range(rows)))
            measured[rows] = registry.get_calls

        # Assert: 行数を 8 倍にしても取得は増えない。
        assert measured[40] - measured[5] == 0, measured
        # 正の対照: 発行 0 件なら差 0 は恒真になる。
        assert measured[5] > 0, measured

    def test_the_enumeration_is_issued_once_per_build(self):
        """`names()` も行ごとに発行しないこと。"""
        # Arrange
        registry = _registry(ma=list(range(50)))

        # Act
        IndicatorTrace(registry).columns_for(list(range(40)))

        # Assert: 1 回の組み立てにつき 1 回（＋検定側の照会は数えない）。
        assert registry.names_calls == 1, registry.names_calls


class TestTheIndicatorTraceStaysFreeOfTheStorageDrivers:
    def test_the_module_imports_neither_pandas_nor_pyarrow(self):
        # Arrange
        import simulator.adapter.trace.indicator_trace as mod

        # Act
        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        # 分岐ではなく内包表記で集める: 入力によって実行経路が変わらないので、
        # 「どの枝を通ったか」に依らず同じ集合が出る（検出範囲は同一）。
        nodes = list(ast.walk(tree))
        imported = {
            alias.name.split(".")[0]
            for n in nodes if isinstance(n, ast.Import) for alias in n.names
        } | {
            n.module.split(".")[0]
            for n in nodes if isinstance(n, ast.ImportFrom) and n.module
        }

        # Assert
        assert imported & {"pandas", "pyarrow"} == set(), imported
        assert imported, "import が 1 件も読めていない（走査が空振り）"
