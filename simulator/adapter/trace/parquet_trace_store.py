"""ParquetTraceStore: どう永続化するか（adapter 層・RUN_TRACE_BASIC_DESIGN §6.5）。

アクター（改訂の動機）: **保存形式・圧縮・ファイル分割**。「何を残すか」「どこを残すか」
とは別の理由で変わるため、モジュールを分ける。

**pandas / pyarrow の唯一の到達点**である（§6.5.3 D-5）。`simulator/adapter/trace/trace_window.py` /
`simulator/adapter/trace/columnar_run_trace.py` /
`simulator/adapter/trace/indicator_trace.py` は列を素の `list` で持ち、ここで初めて
DataFrame 化する。`adapter/trace/__init__.py` が再輸出しないのはこの隔離のためである
（再輸出すると `simulator/adapter/trace/trace_window.py` を import しただけで pyarrow が読まれる）。

なぜ parquet か（設計書 実測 7・憶測ではない）:
    同規模 12 列・乱数（圧縮最悪ケース）で **parquet 61.6MB / JSON 271.1MB**
    （write 0.33s / read 0.08s）。1 ヶ月・実ティックの評価点数は 952,832 点である。

なぜ `TraceStorePort` を作らないか（§6.5.3 YAGNI）:
    保存形式の第 2 実装は要求に無く、JSON は上記の実測で 4.4 倍のサイズと判明済み＝
    採らない。抽象は「差し替えが要る」と分かってから作る。

列の長さが揃わない入力を弾く理由:
    `pandas.DataFrame` は不揃いな列を受け取ると例外を出すが、`None` で埋める経路を
    自前で書くと「取り落とした列が欠測として保存される」ことになり、読み手はそれを
    「その時点の値が無かった」と読む。埋めずに落とす。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

#: 圧縮方式。既定（snappy）を明示して、pandas / pyarrow の既定が変わっても
#: 成果物の形が黙って変わらないようにする。
COMPRESSION = "snappy"


def write_columns(path: Any, columns: Mapping[str, list]) -> int:
    """列集合を parquet 1 本へ書き、**書いた行数**を返す。

    事前条件: ``columns`` は「列名 → 素の `list`」であり、全列が同じ長さを持つ。
        列の並びは呼出側の宣言順（記録列の宣言）であり、ここで並べ替えない。
    事後条件: ``path`` に parquet が 1 本できる。行 0 件でも**ファイルは作る**——
        書かずに済ませると「トレース ON にしたのにファイルが無い」が、書出し失敗と
        「その期間に評価点が無かった」の区別なく起きる。
    例外: 列の長さが揃わない入力は `ValueError`（既定値で黙って埋めない）。
    """
    lengths = {len(values) for values in columns.values()}
    if len(lengths) > 1:
        raise ValueError(
            f"列の長さが揃っていません: "
            f"{ {name: len(v) for name, v in columns.items()} }"
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 列順は宣言順のまま（`DataFrame` は dict の挿入順を保つ）。
    frame = pd.DataFrame(columns)
    frame.to_parquet(path, index=False, compression=COMPRESSION)
    return len(frame)


def read_columns(
    path: Any,
    *,
    columns: "Sequence[str]",
    start: "int | None" = None,
    end: "int | None" = None,
    time_column: str = "time",
) -> "dict[str, list]":
    """窓 `[start, end)`（epoch **ミリ秒**）の**指定列だけ**を素の `list` で返す（§9.2）。

    事前条件:
        ``columns`` は成果物に実在する列名。``start`` / ``end`` は epoch ミリ秒
        （time 列と同じ単位・§9.0）か、両方または片方が ``None``（その側は無制限）。
    事後条件:
        「列名 → 素の `list`」を返す。並びは ``columns`` の宣言順であり、行の並びは
        成果物の並び（記録順）のままである。pandas / pyarrow の型は外へ出さない
        （D-5 の隔離。usecase は素の列を受ける）。
    例外:
        実在しない列名・``start > end`` は `ValueError`（黙って落とさない・§7）。

    **窓と列は IO 段の pushdown で効かせる**（絶対命令・§9.6）。全列・全行を読んでから
    事後スライスする形は「作ってから捨てる」であり、実測 1,036,394 行 × 18 列の成果物
    に対しては画面が描く数千点のために全量を materialise することになる。先例は
    `adapter/repository/tick_parquet.py`（「全列読み→事後スライスは IO を浪費する」）。

    測り方（`simulator/tests/integration/test_parquet_trace_store_read.py`）:
        IO 段（`pd.read_parquet`）へ Test Spy を張り、**materialise した行数 − 返した
        行数 = 0**・**読んだ列 − 返した列 = 0** を表明する。件数そのものは期待値へ
        焼き込まない（焼き込むと浪費が仕様へ昇格する）。

    残る粒度（実測に基づく限界・隠さない）:
        述語 pushdown が物理 IO を削る粒度は **row group** である。行の materialise は
        窓ぶんちょうどになるが、当該 row group の復号は起きる。既定の row group は
        1,048,576 行であり、実測の 1 run（1,036,394 行）は 1 group に収まる＝現状は
        物理 IO の枝刈りが働かない。writer 側の row group 設定は本タスクの範囲外
        （読み口の加法）なので変更していない。
    """
    requested = list(columns)
    if start is not None and end is not None and start > end:
        raise ValueError(
            f"トレースの読み出し窓の開始が終了より後です: start={start} end={end}"
        )
    available = _column_names(path)
    unknown = [name for name in requested if name not in available]
    if unknown:
        raise ValueError(
            f"成果物に存在しない列を要求しました: {unknown}（実在={sorted(available)}）"
        )

    # 窓を判定するのに time 列そのものは要らない（述語は IO 段が評価する）。
    # したがって呼出側が time を要求しなければ読まない＝余分な列を作らない。
    filters = _window_filters(time_column, start, end)
    frame = pd.read_parquet(path, columns=requested, filters=filters)
    # `to_list()` は numpy スカラーを素の int / float / bool へ戻す（D-5 の隔離）。
    return {name: frame[name].to_list() for name in requested}


def time_bounds(
    path: Any, *, time_column: str = "time"
) -> "tuple[Any, Any, int]":
    """`(最小時刻, 最大時刻, 行数)` を **footer の統計だけ**から返す（行を 1 つも読まない）。

    front が窓を選ぶには「この run はどこからどこまで・何行あるか」が要る。それを得る
    ために全行を読むのは「作ってから捨てる」形そのものである（絶対命令）。parquet は
    row group ごとの min / max と総行数を footer に持つので、そこから答える。

    事後条件: 行 0 件の成果物は ``(None, None, 0)`` を返す——「その期間に評価点が
        無かった」を値で読めるようにする（書出し失敗と区別できる状態を保つ・§6.5）。
    """
    # 遅延 import: 書出しだけを使う既存経路へ pyarrow の追加 import を持ち込まない。
    import pyarrow.parquet as pq

    metadata = pq.ParquetFile(path).metadata
    if metadata.num_rows == 0:
        return None, None, 0
    index = metadata.schema.names.index(time_column)
    lows: list = []
    highs: list = []
    for group in range(metadata.num_row_groups):
        statistics = metadata.row_group(group).column(index).statistics
        if statistics is None:  # 統計を持たない成果物は答えを発明しない。
            raise ValueError(
                f"{time_column} 列の統計が成果物に無く、範囲を答えられません: {path}"
            )
        lows.append(statistics.min)
        highs.append(statistics.max)
    return min(lows), max(highs), metadata.num_rows


def _column_names(path: Any) -> "frozenset[str]":
    """成果物が持つ列名（footer のスキーマだけを読む）。"""
    import pyarrow.parquet as pq

    return frozenset(pq.ParquetFile(path).schema_arrow.names)


def _window_filters(
    time_column: str, start: "int | None", end: "int | None"
) -> "list | None":
    """半開 `[start, end)` を IO 段の述語へ翻訳する。両端 `None` は述語なし。

    半開の向き（開始は含み終端は含まない）は本プロジェクトの窓規則
    （datawindow.half_open）と同一である。ここで向きを変えると、記録側の窓
    （`simulator/adapter/trace/trace_window.py`）と読み側の窓が食い違う。
    """
    predicates = []
    if start is not None:
        predicates.append((time_column, ">=", start))
    if end is not None:
        predicates.append((time_column, "<", end))
    return predicates or None


def count_rows_in_window(
    path: Any,
    *,
    start: "int | None" = None,
    end: "int | None" = None,
    time_column: str = "time",
) -> int:
    """窓 `[start, end)` に入る行数を返す（**行を 1 つも materialise しない**）。

    なぜ `read_columns` の結果を数えないか（絶対命令・§9.6）:
        呼出側（`simulator/sim_ui/usecase/query_trace.py`）はこの数を「窓が広すぎるか」
        の判定に使う。読んでから数えると、断るために実測 1,036,394 行を materialise
        することになる＝「作ってから捨てる」形そのものである。parquet の述語評価は
        行を組み立てずに数を答えられるので、そちらへ問う。

    窓の規則（半開の向き）は `_window_filters` ただ 1 つが持つ。ここで書き直すと、
    「数えた窓」と「読んだ窓」が食い違い、上限を通ったのに読むと超える run ができる。
    """
    # 遅延 import: 書出しだけを使う既存経路へ pyarrow の追加 import を持ち込まない。
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq

    dataset = ds.dataset(Path(path), format="parquet")
    filters = _window_filters(time_column, start, end)
    if filters is None:
        return dataset.count_rows()
    # tuple 形式の述語（`read_columns` と**同じもの**）を式へ翻訳する。
    return dataset.count_rows(filter=pq.filters_to_expression(filters))
