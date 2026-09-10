"""指標系列の列挙契約（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.3）。

固定する仕様:

    1. 列挙は **`IndicatorSeriesNamesPort.names()` ただ 1 つ**が持つ（usecase 層・ISP）。
       `IndicatorPort` は `get` / `update` しか持たない——「登録系列を列挙できる」は実行に
       必要な契約ではなく、要求する側（分析・トレース）が別だからである。
    2. 生産実装 2 件（`PandasIndicatorRegistry` / `NullIndicatorRegistry`。`IndicatorPort`
       を**継承**する実装は実測でこの 2 件のみ＝設計書 実測 12）が加法的に実装する。
    3. 未登録参照の公開エラー契約が運ぶ available は `names()` から導く
       （同じ一覧を作る規則が 3 箇所に在った＝設計書 実測 9）。
    4. `EaRegistrySeriesCatalog` は例外プローブをやめて `names()` へ委譲する。

計算量（プロジェクト絶対命令 2026-08-28）:
    測るのは時間ではなく回数。カタログが「捨てるために作る」計算——未登録名を `get` して
    `IndicatorBufferError` を組み立て、その context から名前を読む——を**発行 0** にした
    ことを Spy で表明する。あわせて `names()` の発行数が出力（カタログの件数）だけで
    決まることを、問い合わせ回数の異なる 2 点で固定する（回数そのものは焼き込まない）。

**恒真にしない**: どの表明にも正の対照（測っている対象が 0 件でないこと）を同梱する。
"""
from __future__ import annotations

import inspect

import pytest

from simulator.adapter.indicator.null_registry import NullIndicatorRegistry
from simulator.domain.exceptions import IndicatorBufferError
from simulator.usecase.indicator_catalog_ports import IndicatorSeriesNamesPort
from simulator.usecase.ports import IndicatorPort



def _top_level_declarations(tree: "ast.AST") -> "set[str]":
    """モジュール直下が宣言する名前（関数・クラス・代入）を返す。

    分岐ではなく内包表記で集める: 入力によって実行経路が変わらないので、どの枝を
    通ったかに依らず同じ集合が出る（検出範囲は分岐版と同一）。

    本体の検定（`test_the_catalog_no_longer_declares_a_probe`）と検出器の自己検査
    （`test_the_probe_detector_would_catch_a_reintroduced_probe`）は**同じこの関数**を
    使う。走査を 2 箇所に書くと、自己検査が本体の検出器を守らなくなる。
    """
    import ast

    return {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    } | {
        t.id for n in tree.body if isinstance(n, ast.Assign)
        for t in n.targets if isinstance(t, ast.Name)
    }


def _pandas_registry(names):
    """挿入順を持つ `PandasIndicatorRegistry`（系列の中身は本検定では使わない）。"""
    import pandas as pd

    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    return PandasIndicatorRegistry({n: pd.Series([1.0, 2.0]) for n in names})


# ---- 1: Port は usecase 層に在り、列挙 1 メソッドだけを持つ（ISP）----

class TestTheEnumerationIsItsOwnPort:
    """列挙契約が `IndicatorPort` と分かれていること。"""

    def test_the_port_declares_exactly_one_abstract_method(self):
        # Arrange / Act
        abstracts = IndicatorSeriesNamesPort.__abstractmethods__

        # Assert
        assert abstracts == frozenset({"names"})

    def test_the_port_does_not_widen_the_indicator_port(self):
        """実行に必要な契約（`IndicatorPort`）を太らせていないこと。"""
        # Arrange / Act / Assert
        assert "names" not in IndicatorPort.__abstractmethods__
        assert not issubclass(IndicatorSeriesNamesPort, IndicatorPort)

    def test_the_port_module_imports_nothing_but_the_standard_library(self):
        """usecase 層の Port が実装（pandas 等）へ依存していないこと。"""
        # Arrange
        import ast
        import pathlib

        import simulator.usecase.indicator_catalog_ports as mod

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

        # Assert: stdlib のみ（`__future__` / abc / typing）。
        assert imported <= {"__future__", "abc", "typing"}, imported
        # 正の対照: 走査が空振りしていない。
        assert imported, "import が 1 件も読めていない（走査が空振り）"


# ---- 2: 生産実装 2 件が加法的に実装する ----

class TestBothProductionRegistriesEnumerateTheirSeries:
    """`IndicatorPort` を継承する実装 2 件が `names()` を持つこと。"""

    def test_the_pandas_registry_returns_the_names_in_insertion_order(self):
        """挿入順であること（`sorted` にしない）。

        トレースの列順は「登録された順」であり、名前の辞書順ではない。並べ替えると
        trace_indicators.parquet の列順が registry の宣言順と食い違う。
        """
        # Arrange: 辞書順とは**異なる**挿入順にする（sorted 化を赤にするため）。
        registry = _pandas_registry(["ma", "close", "adx"])

        # Act
        names = registry.names()

        # Assert
        assert names == ("ma", "close", "adx")
        assert isinstance(names, tuple)

    def test_the_null_registry_enumerates_nothing(self):
        # Arrange / Act
        names = NullIndicatorRegistry().names()

        # Assert
        assert names == ()
        assert isinstance(names, tuple)

    @pytest.mark.parametrize(
        "registry_factory",
        [lambda: _pandas_registry(["ma"]), NullIndicatorRegistry],
        ids=["pandas", "null"],
    )
    def test_every_production_registry_satisfies_the_port(self, registry_factory):
        # Arrange / Act
        registry = registry_factory()

        # Assert: 実体で Port を満たす（duck typing ではなく契約で）。
        assert isinstance(registry, IndicatorSeriesNamesPort)
        assert isinstance(registry, IndicatorPort)

    def test_the_two_production_registries_are_the_whole_population(self):
        """`IndicatorPort` を**継承**する生産実装が 2 件だけであること（実測 12）。

        3 件目が生まれたのに `names()` を持たなければ、そのレジストリを使う run の
        指標トレースが黙って空になる。母集団そのものを固定して気づけるようにする。
        """
        # Arrange
        import ast
        import pathlib

        import simulator.adapter.indicator as pkg

        # Act: adapter/indicator/*.py を構文木で走査し `IndicatorPort` を継承する類を集める。
        # 分岐ではなく内包表記で集める（検出範囲は同一・実行経路が入力で変わらない）。
        found = {
            node.name: path.name
            for path in sorted(pathlib.Path(pkg.__file__).parent.glob("*.py"))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ClassDef)
            and any(isinstance(b, ast.Name) and b.id == "IndicatorPort" for b in node.bases)
        }

        # Assert
        assert set(found) == {"PandasIndicatorRegistry", "NullIndicatorRegistry"}, found
        # 各実装が `names` を宣言していること（Port だけ足して実装を忘れた形を赤にする）。
        for cls_name in found:
            cls = getattr(
                __import__(
                    f"simulator.adapter.indicator.{found[cls_name][:-3]}",
                    fromlist=[cls_name],
                ),
                cls_name,
            )
            assert "names" in cls.__dict__, cls_name


# ---- 3: available は names() から導く（同じ一覧を 3 箇所で作らない）----

class TestTheErrorContractDerivesItsAvailableListFromNames:
    """未登録参照の available が `names()` の写しでないこと。"""

    @pytest.mark.parametrize(
        "registry_factory,expected",
        [
            (lambda: _pandas_registry(["ma", "close"]), ["ma", "close"]),
            (NullIndicatorRegistry, []),
        ],
        ids=["pandas", "null"],
    )
    def test_the_available_list_equals_the_enumerated_names(
        self, registry_factory, expected
    ):
        # Arrange
        registry = registry_factory()

        # Act
        with pytest.raises(IndicatorBufferError) as excinfo:
            registry.get("__absent__")

        # Assert
        assert excinfo.value.context["available"] == expected
        assert excinfo.value.context["available"] == list(registry.names())

    def test_the_available_list_is_produced_by_calling_names(self):
        """available の実体が `names()` の呼出であること（写しではない）。

        `names()` を差し替えると available も変わる——これが成り立たないなら、
        一覧を作る規則が 2 つ在る（設計書 実測 9 の是正が効いていない）。
        """
        # Arrange
        from simulator.adapter.indicator.registry import PandasIndicatorRegistry

        class _Renamed(PandasIndicatorRegistry):
            def names(self):
                return ("__patched__",)

        registry = _Renamed({})

        # Act
        with pytest.raises(IndicatorBufferError) as excinfo:
            registry.get("__absent__")

        # Assert
        assert excinfo.value.context["available"] == ["__patched__"]


# ---- 4 ＋ 計算量: カタログは例外プローブを発行しない ----

class _CountingRegistry(IndicatorPort, IndicatorSeriesNamesPort):
    """`get` / `names` の発行回数を数える Spy。"""

    def __init__(self, names):
        self._names = tuple(names)
        self.get_calls = 0
        self.names_calls = 0

    def names(self):
        self.names_calls += 1
        return self._names

    def get(self, name):
        self.get_calls += 1
        raise IndicatorBufferError(
            "未登録の指標参照", context={"name": name, "available": list(self._names)}
        )

    def update(self, bar_index):
        return None


class _ProbeStub:
    """EaBuildProbe（`simulator/sim_ui/adapter/ea_build_probe.py`） の代役。ea_name ごとに同じ Spy を返し、構築回数を数える。"""

    def __init__(self, registry):
        self._registry = registry
        self.builds = 0

    def for_ea(self, ea_name):
        self.builds += 1
        return self._registry


class TestTheCatalogDelegatesToNamesInsteadOfProbingWithAnException:
    """例外プローブ（第 2 実装）が消えていること。"""

    def test_the_catalog_returns_the_enumerated_names(self):
        # Arrange
        from simulator.sim_ui.adapter.ea_registry_series_catalog import (
            EaRegistrySeriesCatalog,
        )

        registry = _CountingRegistry(["ma", "close"])
        catalog = EaRegistrySeriesCatalog(_ProbeStub(registry))

        # Act
        series = catalog.series_for("AnyEa")

        # Assert
        assert series == frozenset({"ma", "close"})

    def test_the_catalog_issues_no_throwaway_get_call(self):
        """計算量: **捨てるために作る計算（例外プローブ）の発行 0**。

        是正前は未登録名を `get` して `IndicatorBufferError` を組み立て、その context を
        読んで捨てていた。名前を得るのに例外を 1 つ作るのは「作ってから捨てる」形であり、
        出力（系列名の集合）は正しいままなので状態検証では原理的に落ちない。
        """
        # Arrange
        from simulator.sim_ui.adapter.ea_registry_series_catalog import (
            EaRegistrySeriesCatalog,
        )

        registry = _CountingRegistry(["ma", "close"])
        catalog = EaRegistrySeriesCatalog(_ProbeStub(registry))

        # Act
        series = catalog.series_for("AnyEa")

        # Assert: 発行（get 呼出）= 0。
        assert registry.get_calls == 0, "例外プローブが残っている（捨てる計算の発行）"
        # 正の対照: 何も返っていないなら上は恒真になる。
        assert series, "系列が 0 件（検定が何も測っていない）"
        assert registry.names_calls > 0, "names() が一度も呼ばれていない"

    def test_the_enumeration_is_issued_once_per_catalogued_ea_not_per_question(self):
        """計算量（オーダー）: 発行数は**出力（カタログ件数）だけ**で決まる。

        問い合わせ回数の異なる 2 点で測る。回数そのものを期待値へ焼き込まない
        （固定するのは「問い合わせを増やしても発行が増えないこと」＝無駄の不在）。
        """
        # Arrange
        from simulator.sim_ui.adapter.ea_registry_series_catalog import (
            EaRegistrySeriesCatalog,
        )

        measured = {}
        for questions in (2, 8):
            registry = _CountingRegistry(["ma"])
            probe = _ProbeStub(registry)
            catalog = EaRegistrySeriesCatalog(probe)

            # Act
            for _ in range(questions):
                catalog.series_for("AnyEa")
            measured[questions] = (registry.names_calls, probe.builds)

        # Assert: 問い合わせを 4 倍にしても発行は増えない。
        assert measured[8][0] - measured[2][0] == 0, measured
        assert measured[8][1] - measured[2][1] == 0, measured
        # 正の対照: 発行が 0 件なら差 0 は恒真になる。
        assert measured[2][0] > 0 and measured[2][1] > 0, measured

    def test_the_catalog_no_longer_declares_a_probe(self):
        """探索用の偽名と例外プローブ関数が**宣言として**消えていること。

        検出は構文木で行う（本文の素朴な文字列検索にしない）。文字列検索は docstring や
        コメントに書かれた説明にも当たり、「何を検出しているか」が検定名の主張と
        食い違う（設計書 §7.2 通過条件 9 と同型の失敗）。
        """
        # Arrange
        import ast

        import simulator.sim_ui.adapter.ea_registry_series_catalog as mod

        # Act: モジュールが宣言する名前（関数・代入）だけを集める。
        tree = ast.parse(inspect.getsource(mod))
        declared = _top_level_declarations(tree)

        # Assert
        assert "_PROBE_NAME" not in declared, "例外プローブ用の偽名が残っている"
        assert "_series_names" not in declared, "例外プローブ関数が残っている"
        # 正の対照: 走査が空振りしていない（宣言を 1 つも読めていないなら上は恒真）。
        assert "EaRegistrySeriesCatalog" in declared, declared

    def test_the_probe_detector_would_catch_a_reintroduced_probe(self):
        """検出器の自己検査: 例外プローブを書き戻した形を実際に赤にできること。"""
        # Arrange
        import ast

        reintroduced = (
            '"""docstring は検出対象ではない。"""\n'
            '_PROBE_NAME = "__probe__"\n'
            "def _series_names(registry):\n    return ()\n"
        )

        # Act
        tree = ast.parse(reintroduced)
        declared = _top_level_declarations(tree)

        # Assert
        assert {"_PROBE_NAME", "_series_names"} <= declared
