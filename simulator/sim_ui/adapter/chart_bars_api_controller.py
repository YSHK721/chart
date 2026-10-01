"""`GET /chart-bars/...` の HTTP 表現 ⇄ 足の問い合わせ UC の変換（adapter 層・ISSUE-552/554 段階 2-1）。

責務（SRP）: **翻訳だけ**。区間の検査も 1 回に返す量の決定も
`simulator/sim_ui/usecase/query_chart_bars.py` が持つ。ここには写さない。

エンドポイント（sim core は prefix 除去後のパスを受ける）:
    GET /chart-bars/{job_id}/extent               行数・列・指標・1 回の上限（宣言）
    GET /chart-bars/{job_id}/rows/{start}/{end}   位置の半開区間 `[start, end)` の列
`start` / `end` は Bar 列の中の位置（0 始まりの整数・ASCII の 10 進数字だけの表現）。

**なぜクエリ文字列でなくパスセグメントか**: sim core の GET はハンドラでクエリを落とす
（`simulator/sim_ui/adapter/trace_api_controller.py` の docstring に実測の記録）。既存の面が
運べる形（パス）に合わせる。

**列名・上限を front へ書かせない**: `extent` の応答が宣言（成果物の列・指標と列の対応・
`MAX_RETURNED_BARS`）をそのまま配る。

出口の消毒（非有限値 → null）と失敗の翻訳（404 / 409 / 413 / 400）は
`simulator/sim_ui/adapter/json_api_translation.py` の実体を使う（分析 API と同じもの）。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.adapter.job_api_controller import ApiResponse
from simulator.sim_ui.adapter.json_api_translation import guarded, json_safe
from simulator.sim_ui.usecase.chart_bars_ports import ChartBarsArtefactMissingError
from simulator.sim_ui.usecase.query_chart_bars import (
    MAX_RETURNED_BARS,
    ChartBarsRangeTooWideError,
)

#: 足の API の根（sim core が受ける prefix 除去後の形）。
CHART_BARS_PATH_PREFIX = "/chart-bars"

_EXTENT_SEGMENT = "extent"
_ROWS_SEGMENT = "rows"


class ChartBarsApiController:
    """`GET /chart-bars/...` の入出力変換。"""

    def __init__(self, *, bars: Any) -> None:
        """``bars``: `extent(job_id)` と `rows(job_id, start=, end=)` を持つ問い合わせ UC。"""
        self._bars = bars

    @property
    def bars(self) -> Any:
        """問い合わせ UC（合成根の検定が実物の結線を確かめるための面）。"""
        return self._bars

    def get(self, path: str) -> ApiResponse:
        """パスを解いて応答を返す。**本メソッドが応答の唯一の出口である**。

        非有限値の null 化はここだけで行う（payload 木ごと通す）。組む場所が複数あると、
        ルートを足した人がそこで消毒を忘れる。
        """
        status, payload = self.route(path)
        return ApiResponse(status, json_safe(payload))

    def route(self, path: str) -> "tuple[int, dict]":
        """パスを解いて `(status, payload)` を返す（消毒前・`ApiResponse` は組まない）。

        公開する理由（観測の境界）: 応答の組み立てが usecase の列を**写さずに**出口へ
        渡していることを、検定が消毒前の payload の同一性で確かめる。
        """
        segments = [s for s in path.split("?", 1)[0].split("/") if s]
        # ["chart-bars", job_id, "extent"] / ["chart-bars", job_id, "rows", start, end]
        if len(segments) < 3 or segments[0] != CHART_BARS_PATH_PREFIX.strip("/"):
            return 404, {"error": "not found"}
        job_id, kind = segments[1], segments[2]

        if kind == _EXTENT_SEGMENT and len(segments) == 3:
            return self._translated(lambda: self._extent(job_id))
        if kind == _ROWS_SEGMENT and len(segments) == 5:
            try:
                start = _position(segments[3])
                end = _position(segments[4])
            except ValueError as exc:
                return 400, {"error": str(exc)}
            return self._translated(lambda: self._rows(job_id, start, end))
        return 404, {"error": "not found"}

    # --- 応答の組み立て ---------------------------------------------------

    def _extent(self, job_id: str) -> "tuple[int, dict]":
        declared = self._bars.extent(job_id)
        return (
            200,
            {
                "ok": True,
                "job_id": job_id,
                "rows": declared.rows,
                "index_column": declared.index_column,
                # 宣言をそのまま配る（front に手書きさせない）。
                "columns": list(declared.columns),
                "indicators": [dict(entry) for entry in declared.indicators],
                "timeframe": declared.timeframe,
                "ea_name": declared.ea_name,
                "dataset_ref": declared.dataset_ref,
                "time_unit": declared.time_unit,
                "max_returned_rows": MAX_RETURNED_BARS,
            },
        )

    def _rows(self, job_id: str, start: int, end: int) -> "tuple[int, dict]":
        answer = self._bars.rows(job_id, start=start, end=end)
        return (
            200,
            {
                "ok": True,
                "job_id": job_id,
                "start": answer.start,
                "end": answer.end,
                "rows": answer.rows,
                # 防御的な写しを作らない。出口の消毒が必ず新しい list を返すので、
                # ここで写すと捨てられるだけの中間リストが列の数だけ増える。
                "columns": answer.columns,
            },
        )

    def _translated(self, call) -> "tuple[int, dict]":
        """UC の失敗を HTTP の状態へ翻訳する（翻訳表の実体は共有・本 API の例外型を渡すだけ）。"""
        return guarded(
            call,
            missing=(ChartBarsArtefactMissingError,),
            too_wide=(ChartBarsRangeTooWideError,),
        )


def _position(token: str) -> int:
    """区間の片側を解く。**ASCII の 10 進数字だけ**の表現を位置（0 以上の整数）として受ける。

    受理する表現の定義はここ 1 か所である。`int` の受理（符号・前後の空白・桁区切りの
    下線・全角やほかの文字体系の数字）に任せない——同じ位置に複数の綴りができる。
    位置の上界の検査は usecase（「`QueryChartBarsInteractor.rows`」）が持つ。ここには写さない。

    解けない字は既定値で埋めない——埋めると「指定していない範囲まで見えた」が静かに起きる。
    """
    try:
        if not (token.isascii() and token.isdigit()):
            raise ValueError(token)
        # 桁数が `int` の変換上限を超える表現も `ValueError` になる（同じ文言へ揃える）。
        return int(token)
    except ValueError:
        raise ValueError(
            f"足の区間の境界は Bar 列の中の位置（0 以上の整数・ASCII の 10 進数字）です: {token[:40]!r}"
        ) from None
