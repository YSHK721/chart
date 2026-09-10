"""ParquetTraceStore: どう永続化するか（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.5）。

固定する仕様:

    1. 列集合（素の `list` の dict）を parquet 1 本へ書き、**書いた行数**を返す。
    2. **pandas / pyarrow の唯一の到達点**である（§6.5.3 D-5）。`simulator/adapter/trace/trace_window.py` /
       `simulator/adapter/trace/columnar_run_trace.py` / `simulator/adapter/trace/indicator_trace.py` はこれらを import しない。
    3. 読み返して同じ値が得られる（書けたが読めない、を作らない）。
    4. `TraceStorePort` を作らない（§6.5.3 YAGNI）: 保存形式の第 2 実装は要求に無く、
       JSON は設計書 実測 7 で 4.4 倍のサイズと判明済み＝採らない。

計算量（プロジェクト絶対命令 2026-08-28）:
    **書いた行数 − 与えた行数 = 0**。加えて、1 回の書出しで生む成果物が**ちょうど 1 本**
    であり、行数を変えても本数が増えないこと（オーダー）を 2 点で固定する。
    回数そのものは期待値へ焼き込まない。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from simulator.adapter.trace.parquet_trace_store import write_columns


def _columns(rows: int) -> "dict[str, list]":
    return {
        "time": [1_704_067_200 + i for i in range(rows)],
        "bar_index": [i // 2 for i in range(rows)],
        "granularity": ["tick"] * rows,
        "halted": [i >= rows // 2 for i in range(rows)],
        "equity": [10_000.0 - i * 1.5 for i in range(rows)],
    }


# ---- 1・3: 書いて読み返せる ----

class TestTheStoreWritesAndReadsBackTheSameColumns:
    """書けたが誰も読めない成果物を作らないこと。"""

    def test_the_written_rows_are_readable_and_identical(self, tmp_path):
        # Arrange
        import pandas as pd

        columns = _columns(7)
        path = tmp_path / "trace_points.parquet"

        # Act
        written = write_columns(path, columns)
        frame = pd.read_parquet(path)

        # Assert: 行数・列名・値がそのまま返る。
        assert written == 7
        assert list(frame.columns) == list(columns)
        for name, values in columns.items():
            assert list(frame[name]) == values, name

    def test_the_column_order_is_preserved(self, tmp_path):
        """列順は与えられた宣言順（COLUMNS）のままであること。"""
        # Arrange
        import pandas as pd

        columns = {"z": [1], "a": [2], "m": [3]}
        path = tmp_path / "ordered.parquet"

        # Act
        write_columns(path, columns)

        # Assert: 辞書順へ並べ替えない。
        assert list(pd.read_parquet(path).columns) == ["z", "a", "m"]

    def test_an_empty_column_set_still_produces_a_readable_file(self, tmp_path):
        """行 0 件（窓の外しか無い run）でも読める成果物になること（境界値）。

        書かずに済ませると「トレース ON にしたのにファイルが無い」が、失敗と
        「その期間に評価点が無かった」の区別なく起きる。
        """
        # Arrange
        import pandas as pd

        columns = {name: [] for name in ("time", "bar_index")}
        path = tmp_path / "empty.parquet"

        # Act
        written = write_columns(path, columns)

        # Assert
        assert written == 0
        frame = pd.read_parquet(path)
        assert list(frame.columns) == ["time", "bar_index"]
        assert len(frame) == 0

    def test_ragged_columns_are_refused_instead_of_being_padded(self, tmp_path):
        """列の長さが揃っていない入力は明示エラー（既定値で黙って埋めない）。"""
        # Arrange
        path = tmp_path / "ragged.parquet"

        # Act / Assert
        with pytest.raises(Exception):
            write_columns(path, {"a": [1, 2, 3], "b": [1]})

    def test_the_output_name_is_flat_and_acceptable_to_the_ledger(self, tmp_path):
        """区切りを含む名前を作らないこと（§6.5 是正 F-3）。

        `FileJobLedger._FILENAME_RE` は区切りを含む名前を受理せず、配信口は
        `_data, job_id, filename` の 3 セグメント固定である。サブディレクトリへ置けば
        「書けたが誰も読めない」成果物になる。
        """
        # Arrange
        from simulator.sim_ui.adapter.file_job_ledger import _FILENAME_RE
        from simulator.sim_ui.adapter.trace_writer import (
            INDICATORS_FILENAME,
            META_FILENAME,
            POINTS_FILENAME,
        )

        # Act / Assert
        for name in (POINTS_FILENAME, INDICATORS_FILENAME, META_FILENAME):
            assert _FILENAME_RE.match(name), name
            assert "/" not in name and "\\" not in name, name


# ---- 2: 技術ドライバの唯一の到達点 ----

class TestTheStoreIsTheOnlyReachPointForTheStorageDrivers:
    """pandas / pyarrow を読むのが `parquet_trace_store` だけであること。"""

    def test_the_store_itself_does_reach_the_drivers(self):
        """正の対照: 走査が「どこにも無い」を測っているのではないこと。"""
        # Arrange
        import simulator.adapter.trace.parquet_trace_store as mod

        # Act
        imported = _all_imported_top_levels(mod.__file__)

        # Assert
        assert imported & {"pandas", "pyarrow"}, imported

    def test_no_other_trace_module_reaches_the_drivers(self):
        # Arrange
        import simulator.adapter.trace as pkg

        directory = pathlib.Path(pkg.__file__).parent

        # Act
        offenders = {
            path.name: _all_imported_top_levels(str(path)) & {"pandas", "pyarrow"}
            for path in sorted(directory.glob("*.py"))
            if path.name != "parquet_trace_store.py"
        }

        # Assert
        assert {k: v for k, v in offenders.items() if v} == {}, offenders
        # 正の対照: 走査対象が実在する（0 ファイルなら上は恒真）。
        assert len(offenders) >= 3, offenders


def _all_imported_top_levels(module_file: str) -> "set[str]":
    tree = ast.parse(pathlib.Path(module_file).read_text(encoding="utf-8"))
    out: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


# ---- 4: 抽象を作らない ----

class TestTheStoreDeclaresNoPort:
    """`TraceStorePort` を作らないこと（§6.5.3 YAGNI）。"""

    def test_no_trace_store_port_class_is_declared(self):
        """トレース永続化の抽象が**クラス宣言として**存在しないこと。

        検出は構文木で行う（本文の素朴な文字列検索にしない）。文字列検索は
        「作らない理由」を説明した docstring にも当たり、検定名の主張と実際の
        検出範囲が食い違う（§7.2 通過条件 9 と同型の失敗）。
        """
        # Arrange
        import simulator.adapter.trace as pkg
        import simulator.usecase as usecase_pkg

        targets = list(pathlib.Path(pkg.__file__).parent.glob("*.py")) + list(
            pathlib.Path(usecase_pkg.__file__).parent.glob("*ports*.py")
        )

        # Act
        # 読み取りと解析を分ける: 表明の対象は**構文木から得たクラス名**であって
        # ソース文字列ではない（文字列に対する表明は本ファイル冒頭で退けた形）。
        # 集約は分岐ではなく内包表記で行う（検出範囲は同一・実行経路が入力で変わらない）。
        trees = [ast.parse(path.read_text(encoding="utf-8")) for path in targets]
        declared = {
            node.name: path.name
            for path, tree in zip(targets, trees)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        }

        # Assert
        offenders = {n: f for n, f in declared.items() if "TraceStore" in n or "TraceWriterPort" == n}
        assert offenders == {}, offenders
        # 正の対照: 走査が空振りしていない（既知のクラスを実際に拾えている）。
        assert "ColumnarRunTrace" in declared, sorted(declared)
        assert "RunTracePort" in declared, sorted(declared)


# ---- 計算量 ----

class TestTheStoreIssuesNoThrowawayWork:
    """書出し 1 回につき成果物 1 本・行数を増やしても本数が増えないこと。"""

    def test_the_written_rows_minus_the_given_rows_is_zero(self, tmp_path):
        # Arrange
        columns = _columns(23)

        # Act
        written = write_columns(tmp_path / "points.parquet", columns)

        # Assert: 発行（書いた行）− 使用（与えた行）= 0。
        assert written - len(columns["time"]) == 0
        # 正の対照: 0 行なら差 0 は恒真になる。
        assert columns["time"], "行が 0 件（検定が何も測っていない）"

    def test_one_call_produces_exactly_one_artefact_at_two_sizes(self, tmp_path):
        # Arrange / Act
        measured = {}
        for rows in (4, 256):
            directory = tmp_path / f"n{rows}"
            directory.mkdir()
            write_columns(directory / "points.parquet", _columns(rows))
            measured[rows] = len(list(directory.iterdir()))

        # Assert: 行数を 64 倍にしても成果物は増えない。
        assert measured[256] - measured[4] == 0, measured
        # 正の対照: 0 本なら差 0 は恒真になる。
        assert measured[4] == 1, measured
