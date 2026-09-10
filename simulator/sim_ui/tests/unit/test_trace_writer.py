"""trace_writer: ジョブ成果物の形（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.5）。

固定する仕様:

    1. 出力は **`job_dir` 直下の平坦名**（是正 F-3）。`FileJobLedger._FILENAME_RE` は
       区切りを含む名前を受理せず、配信口は `_data, job_id, filename` の 3 セグメント
       固定である。サブディレクトリへ置けば「書けたが誰も読めない」成果物になる。
    2. writer は**公開規則を所有しない**。公開可否の関門は FileJobLedger（`simulator/sim_ui/adapter/file_job_ledger.py`） と配信口が
       既に持っており、writer が持つのは**置き場（ファイル名）だけ**である。
    3. job_id も writer が導出しない——Composition Root（`run_job.py`）が `job_dir.name`
       を渡す（`FileJobLedger.job_dir` の規約の 2 つ目の実装を作らない）。
    4. 指標 registry は **Callable で注入**する（是正 D-4）。`simulator.main` を import
       しない（`sim_ui/**` の `main/` 以外は層ゲートで禁じられている）。
    5. trace_meta.json の**列の意味は writer に書き写さない**。各 trace モジュールが
       公開する宣言（`COLUMNS`）を読むだけにする（D-3b）。
    6. `marketdata_window` の有無を記録する（§6.5.2 の既知の不整合を**隠さない**）。

計算量（プロジェクト絶対命令 2026-08-28）:
    書出し 1 回につき成果物は**ちょうど 3 本**であり、記録行数を増やしても本数は増えない。
    指標 registry の供給（注入 Callable）は**1 回だけ**発行する——行ごと・系列ごとに
    引き直すと、出力は同じまま registry の再構築が発行数ぶん起きる（作ってから捨てる形）。
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from simulator.adapter.trace.columnar_run_trace import COLUMNS, ColumnarRunTrace
from simulator.adapter.trace.indicator_trace import BAR_INDEX_COLUMN
from simulator.adapter.trace.trace_window import TraceWindow
from simulator.sim_ui.adapter import trace_writer

_EPOCH = 1_704_067_200
_JOB_ID = "0123456789abcdef0123456789abcdef"


class _Series:
    class _Iloc:
        def __init__(self, values):
            self._values = values

        def __getitem__(self, index):
            return self._values[index]

    def __init__(self, values):
        self.iloc = self._Iloc(list(values))


class _Registry:
    def __init__(self, series):
        self._series = dict(series)

    def names(self):
        return tuple(self._series)

    def get(self, name):
        return self._series[name]


class _Account:
    balance = 10_000.0
    floating_pnl = 0.0
    margin = 0.0
    swap = 0.0
    commission = 0.0

    @property
    def equity(self):
        return self.balance

    def margin_level(self):
        import math

        return math.inf


class _Point:
    granularity = "tick"
    is_synthetic_bar_point = False
    eval_bid = 1.10
    eval_ask = 1.12

    def __init__(self, bar_index, tick_ordinal, epoch):
        self.bar_index = bar_index
        self.tick_ordinal = tick_ordinal
        self.tick_time = epoch
        self.bar = None


def _filled_trace(bars=(0, 0, 1, 2)):
    trace = ColumnarRunTrace(TraceWindow.of(_EPOCH, _EPOCH + 3600))
    for i, bar_index in enumerate(bars):
        trace.observe(_Point(bar_index, i, _EPOCH + i), _Account(), [], False)
    return trace


def _supply(registry):
    """注入される指標 registry 供給（発行回数を数える）。"""
    calls = []

    def supply():
        calls.append(1)
        return registry

    supply.calls = calls
    return supply


def _write(job_dir, trace=None, *, registry=None, marketdata_window=None):
    return trace_writer.write(
        job_dir,
        trace if trace is not None else _filled_trace(),
        job_id=_JOB_ID,
        indicators_supply=_supply(registry if registry is not None else _Registry({})),
        marketdata_window=marketdata_window,
    )


# ---- 1: 平坦名で job_dir 直下へ ----

class TestTheArtefactsLandFlatInsideTheJobDirectory:
    """サブディレクトリを作らないこと（是正 F-3）。"""

    def test_the_three_artefacts_are_written_directly_into_the_job_dir(self, tmp_path):
        # Arrange
        job_dir = tmp_path / _JOB_ID
        job_dir.mkdir()
        registry = _Registry({"ma": _Series([1.0, 2.0, 3.0])})

        # Act
        _write(job_dir, registry=registry)

        # Assert: 3 本が直下に在り、サブディレクトリは 1 つも作られていない。
        names = sorted(p.name for p in job_dir.iterdir())
        assert names == sorted(
            [
                trace_writer.POINTS_FILENAME,
                trace_writer.INDICATORS_FILENAME,
                trace_writer.META_FILENAME,
            ]
        )
        assert [p for p in job_dir.iterdir() if p.is_dir()] == []

    def test_every_filename_is_acceptable_to_the_ledger_and_the_delivery_route(self):
        # Arrange
        from simulator.sim_ui.adapter.file_job_ledger import _FILENAME_RE

        # Act / Assert
        for name in (
            trace_writer.POINTS_FILENAME,
            trace_writer.INDICATORS_FILENAME,
            trace_writer.META_FILENAME,
        ):
            assert _FILENAME_RE.match(name), name


# ---- 2・3: 公開規則も job_id も所有しない ----

class TestTheWriterOwnsNeitherThePublicationRuleNorTheJobId:
    def test_the_job_id_is_taken_from_the_caller_not_derived(self, tmp_path):
        """`job_dir.name` を writer が読み直さないこと。

        読み直すと `FileJobLedger.job_dir` の規約の 2 つ目の実装ができる。渡された
        job_id がディレクトリ名と違っても、writer は渡された方を書く。
        """
        # Arrange: ディレクトリ名と job_id を**わざと**食い違わせる。
        job_dir = tmp_path / "not-the-job-id"
        job_dir.mkdir()

        # Act
        _write(job_dir)

        # Assert
        meta = json.loads((job_dir / trace_writer.META_FILENAME).read_text("utf-8"))
        assert meta["job_id"] == _JOB_ID

    def test_the_module_declares_no_publication_gate(self):
        """公開可否（状態検査・ファイル名の受理規則）を writer が持たないこと。"""
        # Arrange
        source = pathlib.Path(trace_writer.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Act
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

        # Assert: 台帳・配信の関門を持ち込んでいない。
        assert names & {"_FILENAME_RE", "_JOB_ID_RE", "JobStatus", "result_path"} == set()
        # 正の対照: 走査が本モジュールの実体を読めていること。列宣言の参照
        # （`columnar_run_trace.COLUMNS`）を実際に拾えていることで示す。
        # 「集合が非空」では走査対象を取り違えていても通ってしまう。
        assert {"columnar_run_trace", "COLUMNS"} <= names, sorted(names)


# ---- 4: simulator.main を掴まない ----

class TestTheWriterDoesNotReachIntoTheCompositionRoot:
    """`sim_ui/**`（`main/` 以外）→ `simulator.main` の禁止（層ゲート）。"""

    def test_the_module_never_imports_simulator_main(self):
        # Arrange
        tree = ast.parse(
            pathlib.Path(trace_writer.__file__).read_text(encoding="utf-8")
        )

        # Act
        # 分岐ではなく内包表記で集める（検出範囲は同一・実行経路が入力で変わらない）。
        nodes = list(ast.walk(tree))
        imported = {
            alias.name
            for n in nodes if isinstance(n, ast.Import) for alias in n.names
        } | {
            n.module for n in nodes if isinstance(n, ast.ImportFrom) and n.module
        }

        # Assert
        assert {m for m in imported if m == "simulator.main" or m.startswith("simulator.main.")} == set(), imported
        # 正の対照: 走査が空振りしていない。
        assert imported, "import が 1 件も読めていない（走査が空振り）"

    def test_the_indicator_registry_arrives_through_the_injected_callable(self, tmp_path):
        # Arrange
        job_dir = tmp_path / _JOB_ID
        job_dir.mkdir()
        registry = _Registry({"ma": _Series([10.0, 11.0, 12.0])})
        supply = _supply(registry)

        # Act
        trace_writer.write(
            job_dir, _filled_trace(), job_id=_JOB_ID,
            indicators_supply=supply, marketdata_window=None,
        )

        # Assert: 注入された供給が実際に使われている。
        import pandas as pd

        frame = pd.read_parquet(job_dir / trace_writer.INDICATORS_FILENAME)
        assert list(frame.columns) == [BAR_INDEX_COLUMN, "ma"]
        assert list(frame[BAR_INDEX_COLUMN]) == [0, 1, 2]
        assert list(frame["ma"]) == [10.0, 11.0, 12.0]


# ---- 5・6: meta.json の中身 ----

class TestTheMetaRecordsTheFactsWithoutCopyingTheColumnMeanings:
    def test_the_column_declarations_are_read_not_rewritten(self, tmp_path):
        """列名の一覧が各 trace モジュールの宣言そのものであること（D-3b）。"""
        # Arrange
        job_dir = tmp_path / _JOB_ID
        job_dir.mkdir()
        registry = _Registry({"ma": _Series([1.0, 2.0, 3.0])})

        # Act
        _write(job_dir, registry=registry)

        # Assert
        meta = json.loads((job_dir / trace_writer.META_FILENAME).read_text("utf-8"))
        assert meta["columns"]["points"] == list(COLUMNS)
        assert meta["columns"]["indicators"] == [BAR_INDEX_COLUMN, "ma"]

    def test_the_module_does_not_enumerate_the_point_columns_itself(self):
        """writer の構文木に §6.1 の列名リテラルが現れないこと。

        書き写した時点で列の意味が 2 箇所になる（片方だけ増える形の食い違いが
        例外を出さずに起きる）。
        """
        # Arrange
        tree = ast.parse(
            pathlib.Path(trace_writer.__file__).read_text(encoding="utf-8")
        )

        # Act: 文字列リテラルだけを集める（docstring は Expr なので除く）。
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }

        # Assert: 記録列の名前を 1 つも書いていない。
        assert literals & set(COLUMNS) == set(), literals & set(COLUMNS)
        # 正の対照: リテラルの走査が空振りしていない（ファイル名は在る）。
        assert trace_writer.POINTS_FILENAME in literals

    def test_the_meta_records_the_window_and_the_row_counts(self, tmp_path):
        # Arrange
        job_dir = tmp_path / _JOB_ID
        job_dir.mkdir()
        trace = _filled_trace(bars=(0, 0, 1, 2, 2))
        registry = _Registry({"ma": _Series([1.0, 2.0, 3.0])})

        # Act
        result = _write(job_dir, trace, registry=registry)

        # Assert
        meta = json.loads((job_dir / trace_writer.META_FILENAME).read_text("utf-8"))
        assert meta["window"] == {"start": _EPOCH, "end": _EPOCH + 3600}
        assert meta["rows"]["points"] == 5
        assert meta["rows"]["indicators"] == 3
        # 戻り値と meta が同じ事実を運ぶ（呼出側が別経路で数え直さない）。
        assert result["rows"] == meta["rows"]

    def test_an_unbounded_window_is_recorded_as_such(self, tmp_path):
        # Arrange
        job_dir = tmp_path / _JOB_ID
        job_dir.mkdir()
        trace = ColumnarRunTrace(TraceWindow.of(None, None))
        trace.observe(_Point(0, 0, _EPOCH), _Account(), [], False)

        # Act
        _write(job_dir, trace, registry=_Registry({}))

        # Assert: 窓なしは null（0 と混同させない）。
        meta = json.loads((job_dir / trace_writer.META_FILENAME).read_text("utf-8"))
        assert meta["window"] == {"start": None, "end": None}

    @pytest.mark.parametrize(
        "marketdata_window,expected",
        [(None, False), ({"start": 1, "end": 2}, True)],
        ids=["absent", "present"],
    )
    def test_the_meta_does_not_hide_the_known_bar_index_mismatch(
        self, tmp_path, marketdata_window, expected
    ):
        """§6.5.2: `marketdata_window` を伴う run では `bar_index` の指す先が
        指標 registry と bars で一致しない（エンジンに既存の性質）。段階 3 が負う
        義務は**隠さないこと**だけである。
        """
        # Arrange
        job_dir = tmp_path / _JOB_ID
        job_dir.mkdir()

        # Act
        _write(job_dir, marketdata_window=marketdata_window)

        # Assert
        meta = json.loads((job_dir / trace_writer.META_FILENAME).read_text("utf-8"))
        assert meta["marketdata_window"] is expected
        # 誤読への注意が payload 自身に載る（段階 4 の分析面が読む）。
        assert bool(meta.get("indicator_bar_index_is_comparable")) is not expected


# ---- 計算量 ----

class TestTheWriterIssuesNoThrowawayWork:
    def test_the_registry_is_supplied_exactly_once_per_write(self, tmp_path):
        # Arrange
        job_dir = tmp_path / _JOB_ID
        job_dir.mkdir()
        registry = _Registry({"ma": _Series(list(range(10))), "adx": _Series(list(range(10)))})
        supply = _supply(registry)

        # Act
        trace_writer.write(
            job_dir, _filled_trace(bars=tuple(range(8))), job_id=_JOB_ID,
            indicators_supply=supply, marketdata_window=None,
        )

        # Assert: 発行（registry 供給）− 使用（書出し 1 回）= 0。
        assert len(supply.calls) - 1 == 0, len(supply.calls)

    def test_the_artefact_count_does_not_grow_with_the_recorded_rows(self, tmp_path):
        # Arrange / Act
        measured = {}
        for rows in (4, 128):
            job_dir = tmp_path / f"n{rows}"
            job_dir.mkdir()
            trace = _filled_trace(bars=tuple(range(rows)))
            _write(job_dir, trace, registry=_Registry({"ma": _Series(list(range(rows)))}))
            measured[rows] = (len(list(job_dir.iterdir())), trace.rows)

        # Assert: 記録行を 32 倍にしても成果物は増えない。
        assert measured[128][0] - measured[4][0] == 0, measured
        # 正の対照: 記録が 0 件／成果物 0 本なら上は恒真になる。
        assert measured[4][0] == 3, measured
        assert measured[128][1] > measured[4][1], measured
