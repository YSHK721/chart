"""`TracePointsPort` の実装（adapter 層・RUN_TRACE_BASIC_DESIGN §9.3）。

アクター（改訂の動機）: **store への束縛**。「どう読むか」は
`simulator/adapter/trace/parquet_trace_store.py` が、「何を返すか」は
`simulator/sim_ui/usecase/query_trace.py` が持つ。ここが持つのは**その 2 つを繋ぐ結線**
（どのファイルを・どの関門を通って開くか）だけである。

**公開可否の規則を書き直さない**（§9.4「借りるもの」）:
    「完了したジョブに限り結果を公開する」の実体は `usecase/fetch_job_result.py` ただ 1 つで
    あり、`/data/{job_id}/{filename}` の配信口も同じ関門を通っている。ここで別の判定を
    書くと同じ問いに 2 つの答えができ、片方だけ緩む形で必ず食い違う。よって関門を
    **注入で受け**、所在の解決も識別子・ファイル名の受理検査（CWE-22 防御）も
    そちらへ委ねる。

**記録 OFF の run を「0 行」と読ませない**:
    成果物が無いことと、窓に評価点が 0 件だったことは**別の事実**である。0 行で返すと
    front は「その run は何も起きなかった」と読む。前者は
    `TraceArtefactMissingError` で表す。

**run の設定値は spec.json から読む**:
    DD の基準（initial_deposit）も維持率の閾値（stop_out_level）も、分析側が
    発明してよい値ではない——その run が実際に使った値である。spec.json は
    FileJobLedger が job_dir 直下へ書いた投入仕様そのもの（file_job_ledger._spec_of）
    であり、run の入力の単一ソースである。

依存規律: pandas / pyarrow を直接 import しない（store 経由）。素の列だけを usecase へ渡す。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from simulator.adapter.trace import parquet_trace_store
from simulator.sim_ui.adapter.trace_writer import POINTS_FILENAME
from simulator.sim_ui.usecase.trace_query_ports import (
    TraceArtefactMissingError,
    TraceExtent,
    TracePointsPort,
)

#: spec.json のうち分析が読む鍵（build_interactor の引数名と同じ＝投入 API の面）。
_DEPOSIT_KEY = "initial_deposit"
_FLOOR_KEY = "stop_out_level"

#: initial_deposit が spec に無い run の基準。0.0 は mt5_parity の B_0 として
#: 「基準が無い」を表し、DD は peak-to-trough だけで決まる（値を発明しない）。
_NO_DEPOSIT = 0.0


class TraceQuerySource(TracePointsPort):
    """job_dir 直下の trace_points.parquet を読む `TracePointsPort`。"""

    def __init__(self, *, result_gate: Any) -> None:
        """``result_gate``: `execute(job_id, filename) -> Path` を持つ公開可否の関門。

        要求する契約は 1 つだけである——**公開してよい結果の所在を返すか、例外を送出する**。
        既定値を置かない: 関門を渡し忘れた構成が、未完了ジョブの部分成果物を配る形に
        倒れる（組み立ては Composition Root が担う）。
        """
        self._gate = result_gate

    # --- TracePointsPort ------------------------------------------------

    def extent(self, job_id: str) -> TraceExtent:
        path = self._points_path(job_id)
        first, last, rows = parquet_trace_store.time_bounds(path)
        spec = self._spec(job_id)
        # DD の基準が無い run を「基準 0」として黙って分析しない（§7）。
        # equity_dd_absolute は `B_0 - min(equity)` であり、B_0 を発明すると
        # **例外も掲示も出ないまま金額が誤る**。不在は分析できない事実として表す。
        if _DEPOSIT_KEY not in spec:
            raise TraceArtefactMissingError(
                f"ジョブ {job_id} の投入仕様に {_DEPOSIT_KEY} がありません。"
                "ドローダウンの基準を決められないため分析できません"
            )
        floor = spec.get(_FLOOR_KEY)
        return TraceExtent(
            rows=rows,
            first_time=first,
            last_time=last,
            initial_deposit=float(spec[_DEPOSIT_KEY]),
            # 設定が無ければ閾値は無い（0.0 を「割れない閾値」として持ち回らない）。
            # これは値の発明ではなく**定義された不在**である——閾値が無ければ
            # 「割れ」は起こり得ず、事象が 0 件なのが正しい答えである。
            margin_level_floor=None if floor is None else float(floor),
        )

    def count(
        self, job_id: str, *, start: "int | None" = None, end: "int | None" = None
    ) -> int:
        return parquet_trace_store.count_rows_in_window(
            self._points_path(job_id), start=start, end=end
        )

    def read(
        self,
        job_id: str,
        *,
        columns: "Sequence[str]",
        start: "int | None" = None,
        end: "int | None" = None,
    ) -> "dict[str, list]":
        return parquet_trace_store.read_columns(
            self._points_path(job_id), columns=columns, start=start, end=end
        )

    # --- 所在の解決（関門を必ず通る） -------------------------------------

    def _points_path(self, job_id: str) -> Path:
        """点列成果物の所在。**関門を通してから**存在を確かめる。

        順序が重要である: 存在を先に見ると、未完了ジョブの部分成果物について
        「無い」と「公開しない」が入れ替わって漏れる余地ができる。
        """
        path = Path(self._gate.execute(job_id, POINTS_FILENAME))
        if not path.is_file():
            raise TraceArtefactMissingError(
                f"ジョブ {job_id} に実行トレースの成果物がありません"
                f"（{POINTS_FILENAME}）。トレースを ON にして実行してください"
            )
        return path

    def _spec(self, job_id: str) -> "dict[str, Any]":
        """spec.json の `backtest` ブロック。読めない構成は空 dict（値を発明しない）。"""
        path = Path(self._gate.execute(job_id, "spec.json"))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        backtest = payload.get("backtest") if isinstance(payload, dict) else None
        return dict(backtest) if isinstance(backtest, dict) else {}
