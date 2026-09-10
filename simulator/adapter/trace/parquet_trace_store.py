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
from typing import Any, Mapping

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
