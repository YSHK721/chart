"""`ChartBarsPort` の実装（adapter 層・ISSUE-552/554 段階 2-1）。

アクター（改訂の動機）: **store への束縛**。「どう読むか」は
`simulator/adapter/trace/parquet_trace_store.py` が、「何を返すか」は
`simulator/sim_ui/usecase/query_chart_bars.py` が、「何を書くか」は
`simulator/sim_ui/adapter/chart_overlay_writer.py` が持つ。ここが持つのはそれらを繋ぐ結線
（どのファイルを・どの関門を通って開くか）だけである。

**公開可否の規則を書き直さない**: 「完了したジョブに限り結果を公開する」の実体は
`usecase/fetch_job_result.py` ただ 1 つである。関門を注入で受け、所在の解決も識別子・
ファイル名の受理検査（CWE-22 防御）もそちらへ委ねる（「`trace_query_source`」 と同じ形）。

**成果物が無い run を「0 行」と読ませない**: 本段階より前に走らせたジョブは足の成果物を
持たない。0 行で返すと画面は「その run に足が無かった」と読む。
`ChartBarsArtefactMissingError` で表す。

**列名・ファイル名を書き直さない**: 書き手の宣言（定数）と、書き手が成果物の隣に書いた
宣言ファイルから読む。

依存規律: pandas / pyarrow を直接 import しない（store 経由）。素の列だけを usecase へ渡す。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence

from simulator.adapter.trace import parquet_trace_store
from simulator.sim_ui.adapter.chart_overlay_writer import (
    CHART_BARS_DECLARATION_FILENAME,
    CHART_BARS_FILENAME,
    INDEX_COLUMN,
)
from simulator.sim_ui.usecase.chart_bars_ports import (
    ChartBarsArtefactMissingError,
    ChartBarsDeclaration,
    ChartBarsPort,
)


def _level_of(payload: dict) -> "float | None":
    """宣言のストップアウト水準。鍵が無い（書く前に実行したジョブ）は `None`。

    鍵が在るのに有限の数でなければ読めない宣言として扱う（既定値で埋めない）。
    """
    if "stop_out_level" not in payload:
        return None
    level = payload["stop_out_level"]
    if isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level):
        raise ValueError(f"stop_out_level が有限の数ではありません: {level!r}")
    return float(level)


class ChartBarsSource(ChartBarsPort):
    """job_dir 直下の chart_bars.parquet とその宣言を読む `ChartBarsPort`。"""

    def __init__(self, *, result_gate: Any) -> None:
        """``result_gate``: `execute(job_id, filename) -> Path` を持つ公開可否の関門。

        既定値を置かない: 関門を渡し忘れた構成が、未完了ジョブの部分成果物を配る形に倒れる
        （組み立ては Composition Root が担う）。
        """
        self._gate = result_gate

    # --- ChartBarsPort --------------------------------------------------

    def declaration(self, job_id: str) -> ChartBarsDeclaration:
        path = self._published(job_id, CHART_BARS_DECLARATION_FILENAME)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return ChartBarsDeclaration(
                rows=int(payload["rows"]),
                index_column=str(payload["index_column"]),
                columns=tuple(payload["columns"]),
                indicators=tuple(dict(entry) for entry in payload["indicators"]),
                timeframe=payload["timeframe"],
                ea_name=payload["ea_name"],
                dataset_ref=payload["dataset_ref"],
                time_unit=payload["time_unit"],
                stop_out_level=_level_of(payload),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # 読めない宣言を既定値で埋めない（列を発明すると別の列を足として描く）。
            raise ChartBarsArtefactMissingError(
                f"ジョブ {job_id} の足の成果物の宣言を読めません"
                f"（{CHART_BARS_DECLARATION_FILENAME}: {exc!r}）"
            ) from None

    def count(self, job_id: str, *, start: int, end: int) -> int:
        return parquet_trace_store.count_rows_in_window(
            self._published(job_id, CHART_BARS_FILENAME),
            start=start, end=end, time_column=INDEX_COLUMN,
        )

    def read(
        self, job_id: str, *, columns: "Sequence[str]", start: int, end: int
    ) -> "dict[str, list]":
        return parquet_trace_store.read_columns(
            self._published(job_id, CHART_BARS_FILENAME),
            columns=columns, start=start, end=end, time_column=INDEX_COLUMN,
        )

    # --- 所在の解決（関門を必ず通る） -------------------------------------

    def _published(self, job_id: str, filename: str) -> Path:
        """成果物の所在。**関門を通してから**存在を確かめる。

        順序が重要である: 存在を先に見ると、未完了ジョブの部分成果物について
        「無い」と「公開しない」が入れ替わって漏れる余地ができる。
        """
        path = Path(self._gate.execute(job_id, filename))
        if not path.is_file():
            raise ChartBarsArtefactMissingError(
                f"ジョブ {job_id} に足の成果物がありません（{filename}）。"
                "このジョブを実行し直してください"
            )
        return path
