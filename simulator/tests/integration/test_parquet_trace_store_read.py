"""parquet_trace_store の読み口（RUN_TRACE_BASIC_DESIGN §9.2/§9.3/§9.6）。

**計算量テスト（絶対命令・例外なし）を含む。** 固定するのは出力の正しさではなく
**無駄の不在**である:

    読んだ行数 − 返した行数 = 0
    読んだ列   − 返した列   = 0

「作ってから捨てる」欠陥は出力が正しいままなので状態検証では原理的に落ちない
（ISSUE-450 で既存 1,233 件が緑のまま 20 日間浪費を保護した）。ここで測るのは
**回数（行数・列数）であって時間ではない**。時間の閾値はマシン負荷で揺れ、緩んで浪費を通す。

実測に基づく必要性（憶測ではない・2026-09-10）:
    実ティック 1 ヶ月 run（JP225 2026-01）の trace_points.parquet は
    **1,036,394 行 × 18 列 / 9.7MB**。画面が描くのは数千点であり、全量を読んで
    front で捨てる形は絶対命令に反する。

先例: `adapter/repository/tick_parquet.py` の `pd.read_parquet(..., columns=...)`
（列 pushdown を IO 段で効かせる。全列読み→事後スライスは IO を浪費する、と明記）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from simulator.adapter.trace import parquet_trace_store as store

_T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z（epoch **ミリ秒**・§9.0）
_STEP_MS = 250

#: trace_points.parquet の実列（`columnar_run_trace.COLUMNS` から導出する。
#: 名前の一覧をテストへ書き写すと 18 列の宣言が 2 箇所になる）。
def _all_columns() -> "tuple[str, ...]":
    from simulator.adapter.trace.columnar_run_trace import COLUMNS

    return COLUMNS


def _write(path, rows: int):
    """`rows` 行の点列を実writerで書く（読み口だけを差し替えても嘘がつけないように）。"""
    columns = {name: [] for name in _all_columns()}
    for i in range(rows):
        columns["time"].append(_T0 + i * _STEP_MS)
        columns["bar_index"].append(i // 4)
        columns["tick_ordinal"].append(i % 4)
        columns["granularity"].append("tick")
        columns["is_synthetic"].append(False)
        columns["eval_bid"].append(1.0 + i * 0.001)
        columns["eval_ask"].append(1.002 + i * 0.001)
        columns["balance"].append(10_000.0)
        columns["equity"].append(10_000.0 - (i % 17))
        columns["floating_pnl"].append(-(i % 17))
        columns["margin"].append(100.0)
        columns["margin_level"].append(1_000.0 - (i % 17))
        columns["swap"].append(0.0)
        columns["commission"].append(0.0)
        columns["open_count"].append(i % 3)
        columns["open_volume_buy"].append(float(i % 3))
        columns["open_volume_sell"].append(0.0)
        columns["halted"].append(i > rows - 3)
    written = store.write_columns(path, columns)
    assert written == rows
    return path


class _ReadSpy:
    """IO 段（`pd.read_parquet`）へ張る Test Spy。

    数えるのは **materialise された行数と要求された列**である。実装が窓の外や
    余分な列を読んでから捨てても、返り値は正しいままなので状態検証では落ちない。
    """

    def __init__(self, real):
        self._real = real
        self.calls: "list[dict]" = []

    def __call__(self, path, *args, **kwargs):
        frame = self._real(path, *args, **kwargs)
        self.calls.append(
            {
                "columns": tuple(kwargs.get("columns") or ()),
                "filters": kwargs.get("filters"),
                "rows": len(frame),
            }
        )
        return frame

    @property
    def rows_read(self) -> int:
        return sum(call["rows"] for call in self.calls)

    @property
    def columns_read(self) -> "set[str]":
        names: "set[str]" = set()
        for call in self.calls:
            names |= set(call["columns"])
        return names


@pytest.fixture
def spy(monkeypatch):
    spy = _ReadSpy(pd.read_parquet)
    monkeypatch.setattr(store.pd, "read_parquet", spy)
    return spy


# ---- 1: 何を返すか（正しさ。計算量の前に正の対照を立てる） ----

class TestTheSliceReturnsTheRequestedColumnsInsideTheWindow:
    def test_the_window_is_half_open_in_epoch_millis(self, tmp_path):
        # Arrange: 4 行ぶんの窓 [T0+1000, T0+2000)（250ms 刻みなので 4 行）。
        path = _write(tmp_path / "p.parquet", 40)
        start, end = _T0 + 1_000, _T0 + 2_000

        # Act
        got = store.read_columns(
            path, columns=("time", "equity"), start=start, end=end
        )

        # Assert
        assert list(got) == ["time", "equity"]
        assert got["time"] == [start + i * _STEP_MS for i in range(4)]
        # 半開: 終端ちょうどは含まない。
        assert end not in got["time"]
        # 正の対照: 0 行なら窓の表明が恒真になる。
        assert len(got["time"]) == 4

    def test_an_absent_window_returns_every_row(self, tmp_path):
        # Arrange
        path = _write(tmp_path / "p.parquet", 12)

        # Act
        got = store.read_columns(path, columns=("time",))

        # Assert
        assert len(got["time"]) == 12

    def test_the_values_are_plain_lists_not_frames(self, tmp_path):
        """pandas を adapter の外へ出さない（D-5 の隔離・usecase は素の列を受ける）。"""
        # Arrange
        path = _write(tmp_path / "p.parquet", 5)

        # Act
        got = store.read_columns(path, columns=("time", "halted"))

        # Assert
        assert isinstance(got, dict)
        assert all(type(v) is list for v in got.values())
        assert all(type(t) is int for t in got["time"])
        assert all(type(h) is bool for h in got["halted"])

    def test_an_unknown_column_fails_loudly(self, tmp_path):
        """存在しない列を黙って落とさない（§7「既定値で黙って埋めない」）。"""
        # Arrange
        path = _write(tmp_path / "p.parquet", 5)

        # Act / Assert
        with pytest.raises(ValueError):
            store.read_columns(path, columns=("time", "no_such_column"))

    def test_a_reversed_window_fails_loudly(self, tmp_path):
        # Arrange
        path = _write(tmp_path / "p.parquet", 5)

        # Act / Assert: 空の結果で成功させると「窓を間違えたのに 0 行で成功」になる。
        with pytest.raises(ValueError):
            store.read_columns(path, columns=("time",), start=_T0 + 10, end=_T0)


# ---- 2: 計算量テスト（絶対命令・§9.6） ----

class TestNothingIsReadAndThenDiscarded:
    """読んだ行数 − 返した行数 = 0／読んだ列 − 返した列 = 0。"""

    def test_no_row_outside_the_window_is_materialised(self, tmp_path, spy):
        # Arrange: 窓の外に 10 倍の行を置く（全量読み→事後スライスなら 10 倍読む）。
        path = _write(tmp_path / "p.parquet", 400)
        start, end = _T0 + 10_000, _T0 + 11_000  # 4 行

        # Act
        got = store.read_columns(
            path, columns=("time", "equity"), start=start, end=end
        )

        # Assert: 発行（materialise）した行 − 返した行 = 0。
        returned = len(got["time"])
        assert spy.rows_read - returned == 0, (spy.rows_read, returned)
        # 正の対照: 返した行が 0 なら上式は 0-0 で恒真になる。
        assert returned == 4

    def test_no_column_outside_the_request_is_materialised(self, tmp_path, spy):
        # Arrange: 実列は 18 列。要求は 2 列。
        path = _write(tmp_path / "p.parquet", 40)

        # Act
        got = store.read_columns(path, columns=("time", "equity"))

        # Assert: 読んだ列 − 返した列 = 0（列 pushdown が効いている）。
        assert spy.columns_read == set(got), (spy.columns_read, set(got))
        # 正の対照: 実列がもっと多い状況で測っている。
        assert len(_all_columns()) > len(got) >= 2

    @pytest.mark.parametrize("total_rows", [200, 2_000])
    def test_the_cost_is_set_by_the_window_not_by_the_run_length(
        self, tmp_path, spy, total_rows
    ):
        """窓を固定したまま run 長を変えても、読む量・返す量が増えない（オーダーの表明）。

        件数そのものを期待値へ焼き込まない（焼き込むと浪費が仕様へ昇格する・ISSUE-450）。
        固定するのは「run 長に依存しないこと」だけであり、比較は下の
        `test_two_run_lengths_agree` が 2 点で行う。
        """
        # Arrange
        path = _write(tmp_path / f"p{total_rows}.parquet", total_rows)
        start, end = _T0 + 1_000, _T0 + 2_000

        # Act
        got = store.read_columns(
            path, columns=("time", "equity"), start=start, end=end
        )

        # Assert
        assert spy.rows_read == len(got["time"]) == 4

    def test_two_run_lengths_agree_on_the_read_and_returned_counts(self, tmp_path, monkeypatch):
        """2 点（run 長 200 / 2,000）で読む量・返す量が一致する。"""
        # Arrange
        start, end = _T0 + 1_000, _T0 + 2_000
        observed = []

        for total in (200, 2_000):
            path = _write(tmp_path / f"len{total}.parquet", total)
            spy = _ReadSpy(pd.read_parquet)
            monkeypatch.setattr(store.pd, "read_parquet", spy)
            # Act
            got = store.read_columns(
                path, columns=("time", "equity"), start=start, end=end
            )
            observed.append((spy.rows_read, len(got["time"])))
            monkeypatch.undo()

        # Assert: run 長が 10 倍でも読む量・返す量は同じ。
        assert observed[0] == observed[1], observed
        # 正の対照: 0 行同士の一致を見ていない。
        assert observed[0][1] > 0

    def test_the_predicate_reaches_the_io_layer(self, tmp_path, spy):
        """窓が IO 段の述語として渡っていること（構造の表明）。

        行数の一致だけでは「全量を読んでから捨てた」実装を、行数が偶然一致する
        入力で取り落とす余地が残る。窓が filters として IO へ届いていることを
        直接見る。
        """
        # Arrange
        path = _write(tmp_path / "p.parquet", 40)

        # Act
        store.read_columns(
            path, columns=("time",), start=_T0 + 1_000, end=_T0 + 2_000
        )

        # Assert
        assert spy.calls and spy.calls[0]["filters"], spy.calls


# ---- 3: 範囲の問い合わせ（行を 1 つも読まない） ----

class TestTheExtentIsReadFromTheFooterOnly:
    """行数・時刻の下端上端は parquet の footer 統計から得る（行を読まない）。

    front が窓を選ぶには「この run はどこからどこまで・何行あるか」が要る。それを
    得るために 1,036,394 行を読むのは「作ってから捨てる」形そのものである。
    """

    def test_it_reports_the_row_count_and_the_time_bounds(self, tmp_path):
        # Arrange
        path = _write(tmp_path / "p.parquet", 40)

        # Act
        got = store.time_bounds(path)

        # Assert
        assert got == (_T0, _T0 + 39 * _STEP_MS, 40)

    def test_it_materialises_no_row_at_all(self, tmp_path, spy):
        # Arrange
        path = _write(tmp_path / "p.parquet", 400)

        # Act
        rows = store.time_bounds(path)[2]

        # Assert: 行数を答えるのに 1 行も読んでいない。
        assert spy.rows_read == 0, spy.calls
        # 正の対照: 答えは空ではない。
        assert rows == 400

    def test_an_empty_file_reports_no_bounds(self, tmp_path):
        """行 0 件でもファイルは作られる（`write_columns` の事後条件）。"""
        # Arrange
        path = tmp_path / "empty.parquet"
        store.write_columns(path, {name: [] for name in _all_columns()})

        # Act
        got = store.time_bounds(path)

        # Assert: 「その期間に評価点が無かった」を値で読めるようにする。
        assert got == (None, None, 0)


# ---- 4: 窓の件数（行を読まずに数える） ----

class TestTheWindowRowCountIsAnsweredWithoutReadingRows:
    """**計算量テスト**: 「窓が広すぎるか」を読んでから判定しない。

    読んでから断るのは「作ってから捨てる」形そのものである。実測 1,036,394 行の
    成果物に対しては、断るために全量を materialise することになる。
    """

    def test_it_counts_the_rows_inside_the_window(self, tmp_path):
        # Arrange
        path = _write(tmp_path / "p.parquet", 40)

        # Act
        got = store.count_rows_in_window(
            path, start=_T0 + 1_000, end=_T0 + 2_000
        )

        # Assert
        assert got == 4

    def test_it_counts_every_row_without_a_window(self, tmp_path):
        # Arrange
        path = _write(tmp_path / "p.parquet", 40)

        # Act / Assert
        assert store.count_rows_in_window(path) == 40

    def test_it_materialises_no_row(self, tmp_path, spy):
        # Arrange
        path = _write(tmp_path / "p.parquet", 400)

        # Act
        got = store.count_rows_in_window(path, start=_T0 + 1_000, end=_T0 + 2_000)

        # Assert
        assert spy.rows_read == 0, spy.calls
        # 正の対照: 答えは 0 ではない（数えていないだけ、ではない）。
        assert got == 4

    def test_the_count_and_the_read_agree_on_the_window(self, tmp_path):
        """件数の窓と読みの窓が同じ規則（半開）であること。

        食い違うと「上限を通ったのに読むと超える」（または逆）が起きる。
        """
        # Arrange
        path = _write(tmp_path / "p.parquet", 60)
        start, end = _T0 + 3_000, _T0 + 7_250

        # Act
        counted = store.count_rows_in_window(path, start=start, end=end)
        read = store.read_columns(path, columns=("time",), start=start, end=end)

        # Assert
        assert counted == len(read["time"])
        assert counted > 0

    def test_the_window_rule_is_declared_in_one_place(self):
        """半開の規則は _window_filters ただ 1 つが持つ（写しを作らない）。"""
        # Arrange
        import ast
        import pathlib

        source = pathlib.Path(store.__file__).read_text(encoding="utf-8")

        # Act: 比較演算子の文字列リテラル（">=" / "<"）が現れる箇所を数える。
        literals = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and node.value in (">=", "<")
        ]

        # Assert: 窓の向きを表すリテラルは 1 関数の中にしかない。
        assert len(literals) == 2, [n.lineno for n in literals]
