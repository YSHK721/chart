"""bench の日列挙・半開述語が共有の唯一実体を読むことを固定する（ISSUE-407）。

何が問題だったか（実測 2026-08-18 起票）:
    bench_run の partition 述語構築が日列挙と半開境界の解釈を手書き複製しており
    （lo.normalize() 起点の while ループ）、境界解釈が第 3 系統になっていた。
    Bar / Candle / Tick 段は datawindow.half_open へ統合済みであり、複製は
    「同じコードを手書き複製するな」規約（単一ソース化）の違反である。

複製が実際に生んでいた浪費（本モジュールの Red で実測した欠陥）:
    - 空窓（lo == hi）でも lo の属する日を列挙し、読む必要のない part を 1 つ開く。
    - 逆転窓（lo > hi）では述語が None になり、**全 part** を開いてから全行を捨てる。
    共有実体（半開・空窓は日ゼロ）へ寄せることで両方が原因ごと消える。

本モジュールが固定する契約:
  1. 単一ソース: bench が読む日列挙は tick 段の唯一実体と**同一オブジェクト**である。
     境界の正規化は datawindow.half_open（唯一実体）で行う。
  2. 空窓・逆転窓は part を 1 つも開かない（発行 0・使用 0）。
  3. 半開境界: end がちょうど日境界のとき end 当日の part を開かない。
  4. 計算量（発行 − 使用 = 0）: 開いた part はすべて出力行に使われる。
     開く part 数は窓が覆う日数だけで決まり、日あたりの行数を増やしても増えない
     （オーダーの表明。回数リテラルは焼き込まない）。

構造: Arrange-Act-Assert。書込は tmp_path 配下のみ。
"""
from __future__ import annotations

import pandas as pd
import pytest

from simulator.tools.bench.bench_run import read_parquet_pruned, write_parquet_partitioned


def _tick_frame(days: int, ticks_per_day: int) -> pd.DataFrame:
    """2025-01-01 起点で days 日 × ticks_per_day 行の最小 tick frame を作る。"""
    stamps = [
        pd.Timestamp("2025-01-01") + pd.Timedelta(days=d, minutes=i)
        for d in range(days)
        for i in range(ticks_per_day)
    ]
    return pd.DataFrame({"timestamp": stamps, "bid": [100.0] * len(stamps)})


@pytest.fixture()
def day_store(tmp_path):
    """4 日分・日 partition の parquet store（bench 自身の writer で書く）。"""
    root = tmp_path / "pq_day"
    write_parquet_partitioned(_tick_frame(days=4, ticks_per_day=3), root, by_hour=False)
    return root


class TestSingleSource:
    """契約 1: 日列挙は tick 段の唯一実体・境界正規化は datawindow の唯一実体。"""

    def test_bench_reads_the_shared_day_enumeration_object(self):
        from simulator.adapter.repository import _tick_frame as shared
        from simulator.tools.bench import bench_run

        assert bench_run._utc_day_partitions is shared._date_predicate

    def test_bench_normalizes_bounds_with_the_shared_half_open_window(self):
        import ast
        import inspect

        from simulator.tools.bench import bench_run

        # 述語構築の関数本体が共有実体 HalfOpenEpochWindow を呼ぶ（正規化点の所在）。
        tree = ast.parse(inspect.getsource(bench_run._date_predicate))
        names = {
            node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
            for node in ast.walk(tree)
        }
        assert "from_datetimes" in names


class TestEmptyAndInvertedWindowsReadNothing:
    """契約 2: 空窓・逆転窓は part を 1 つも開かない（是正前は 1 個 / 全部を開いていた）。"""

    def test_an_empty_window_opens_no_parts(self, day_store):
        moment = pd.Timestamp("2025-01-02 12:00")
        result = read_parquet_pruned(day_store, lo=moment, hi=moment)
        assert (result["files"], result["rows"]) == (0, 0)

    def test_an_inverted_window_opens_no_parts(self, day_store):
        result = read_parquet_pruned(
            day_store, lo=pd.Timestamp("2025-01-03"), hi=pd.Timestamp("2025-01-01")
        )
        assert (result["files"], result["rows"]) == (0, 0)


class TestHalfOpenBoundary:
    """契約 3: end がちょうど日境界なら end 当日の part は開かない。"""

    def test_a_midnight_end_excludes_the_end_day(self, day_store):
        result = read_parquet_pruned(
            day_store, lo=pd.Timestamp("2025-01-01"), hi=pd.Timestamp("2025-01-03")
        )
        # 01-01 / 01-02 の 2 part・各 3 行。01-03 の part は開かない。
        assert (result["files"], result["rows"]) == (2, 6)

    def test_one_second_past_midnight_includes_the_end_day(self, day_store):
        result = read_parquet_pruned(
            day_store,
            lo=pd.Timestamp("2025-01-01"),
            hi=pd.Timestamp("2025-01-03 00:00:01"),
        )
        # end 日の part は開くが、行フィルタは半開のまま（00:00:00 の 1 行のみ通す）。
        assert (result["files"], result["rows"]) == (3, 7)


class TestNoWastedPartReads:
    """契約 4: 開いた part はすべて出力に使われ、開く数は日数だけで決まる。"""

    def test_every_opened_part_contributes_rows(self, day_store):
        # 窓が完全に覆う 2 日: 開いた part 数 × 日あたり行数 = 出力行数（発行 − 使用 = 0）。
        result = read_parquet_pruned(
            day_store, lo=pd.Timestamp("2025-01-02"), hi=pd.Timestamp("2025-01-04")
        )
        assert result["rows"] == result["files"] * 3

    def test_more_ticks_per_day_do_not_open_more_parts(self, tmp_path):
        # オーダーの表明: part の発行数は窓が覆う日数の関数であり、行密度に依存しない。
        window = (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-03"))
        opened = []
        for label, tpd in (("sparse", 2), ("dense", 40)):
            root = tmp_path / label
            write_parquet_partitioned(
                _tick_frame(days=4, ticks_per_day=tpd), root, by_hour=False
            )
            opened.append(read_parquet_pruned(root, *window)["files"])
        assert opened[0] == opened[1]
