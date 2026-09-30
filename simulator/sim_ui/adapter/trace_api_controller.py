"""`GET /trace/...` の HTTP 表現 ⇄ 分析 UC の変換（adapter 層・RUN_TRACE_BASIC_DESIGN §9.2/§9.3）。

責務（SRP）: **翻訳だけ**。窓・列・粒度の解釈も返す量の決定も
`simulator/sim_ui/usecase/query_trace.py` が持ち、事象の意味は
`simulator/sim_ui/usecase/derive_trace_events.py` が持つ。ここには写さない
（settings_schema_api_controller と同型）。

エンドポイント（sim core は prefix 除去後のパスを受ける）:
    GET /trace/{job_id}/extent                  記録の範囲・run の設定値・宣言
    GET /trace/{job_id}/points/{start}/{end}    窓の点列・事象・DD
`start` / `end` は epoch **ミリ秒**の整数、または `-`（その側は無制限）。

**なぜクエリ文字列でなくパスセグメントか（実測・2026-09-10）**:
    sim core の GET は serve_sim.make_handler が
    ``app.static_server.serve(self, urlparse(self.path).path)`` を呼ぶため、
    **クエリはハンドラで落ちる**。既存の GET JSON ルート 3 本（`/settings-schema` /
    `/run-options` / `/ea-series/{ea_name}`）もクエリを受け取っていない。受けるには
    Phase 1 のハンドラを変える必要があり、それは「内側を 1 バイトも変えない」という
    本段階の制約に反する。迂回ではなく、既存の面が運べる形（パス）に合わせる。

**列の一覧・上限・事象種別を front へ書かせない**:
    `extent` の応答が宣言（`ANALYSIS_COLUMNS` / `MAX_RETURNED_ROWS` / `EVENT_KINDS`）を
    そのまま配る。front が手書きすれば、同じ宣言が 2 箇所になり片方だけ取り残される
    （`sim_tabs_view.test.js` の手書き期待値と同型の欠陥）。

JSON 直列化は既存 `job_api_controller.ApiResponse` を再利用する（同型の to_bytes を書かない）。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.adapter.job_api_controller import ApiResponse
# 出口の消毒と失敗の翻訳は共有の実体を使う（足の API `/chart-bars` と同じもの・写さない）。
from simulator.sim_ui.adapter.json_api_translation import (
    guarded,
    json_safe as _json_safe,
)
from simulator.sim_ui.usecase.derive_trace_events import EVENT_KINDS
from simulator.sim_ui.usecase.query_trace import (
    ANALYSIS_COLUMNS,
    MAX_RETURNED_ROWS,
    TraceWindowTooWideError,
)
from simulator.sim_ui.usecase.trace_query_ports import TraceArtefactMissingError

#: 分析 API の根（sim core が受ける prefix 除去後の形）。
TRACE_PATH_PREFIX = "/trace"
#: 窓の片側を「無制限」と書くためのトークン（epoch ミリ秒の整数と紛れない字）。
UNBOUNDED_TOKEN = "-"

_EXTENT_SEGMENT = "extent"
_POINTS_SEGMENT = "points"


class TraceApiController:
    """`GET /trace/...` の入出力変換。"""

    def __init__(self, *, trace: Any) -> None:
        """``trace``: `extent(job_id)` と `analyse(job_id, start=, end=)` を持つ分析 UC。"""
        self._trace = trace

    @property
    def trace(self) -> Any:
        """分析 UC（合成根の検定が実物の結線を確かめるための面）。"""
        return self._trace

    def get(self, path: str) -> ApiResponse:
        """パスを解いて応答を返す。解けない形は 404（推測で補完しない）。

        **本メソッドが応答の唯一の出口である**（工程 5 レビュー 🔴-4 の是正）。内側は
        `(status, payload)` を返すだけで `ApiResponse` を組まない。組む場所が複数あると、
        ルートを足した人がそこで消毒を忘れる——それが実際に起きたことである
        （`_points` へは手書きで撒いたが `/extent` に忘れ、front が最初に叩く 1 本が
        `Infinity` で JSON.parse に失敗した）。出口を 1 つに閉じれば忘れる場所が無い。
        出口が 1 つであることは `test_trace_api_controller.py` が構文木で固定する。
        """
        status, payload = self._route(path)
        # 非有限値の null 化はここ**だけ**で行う（payload 木を再帰的に通す）。
        return ApiResponse(status, _json_safe(payload))

    def _route(self, path: str) -> "tuple[int, dict]":
        """パスを解いて `(status, payload)` を返す（`ApiResponse` は組まない）。"""
        segments = [s for s in path.split("?", 1)[0].split("/") if s]
        # ["trace", job_id, "extent"] / ["trace", job_id, "points", start, end]
        if len(segments) < 3 or segments[0] != TRACE_PATH_PREFIX.strip("/"):
            return 404, {"error": "not found"}
        job_id, kind = segments[1], segments[2]

        if kind == _EXTENT_SEGMENT and len(segments) == 3:
            return self._guarded(lambda: self._extent(job_id))
        if kind == _POINTS_SEGMENT and len(segments) == 5:
            try:
                start = _bound(segments[3])
                end = _bound(segments[4])
            except ValueError as exc:
                return 400, {"error": str(exc)}
            return self._guarded(lambda: self._points(job_id, start, end))
        return 404, {"error": "not found"}

    # --- 応答の組み立て ---------------------------------------------------

    def _extent(self, job_id: str) -> "tuple[int, dict]":
        extent = self._trace.extent(job_id)
        return (
            200,
            {
                "ok": True,
                "job_id": job_id,
                "rows": extent.rows,
                "first_time": extent.first_time,
                "last_time": extent.last_time,
                "initial_deposit": extent.initial_deposit,
                "margin_level_floor": extent.margin_level_floor,
                # 最初に問うべき窓。front に幅を推測させない（実測で 413 を踏んだ）。
                "suggested_window": {
                    "start": extent.suggested_window[0],
                    "end": extent.suggested_window[1],
                },
                # 宣言をそのまま配る（front に手書きさせない）。
                "columns": list(ANALYSIS_COLUMNS),
                "event_kinds": list(EVENT_KINDS),
                "max_returned_rows": MAX_RETURNED_ROWS,
                # 時刻の単位を payload 自身が名乗る（front が推測しない）。
                "time_unit": "epoch_millis",
            },
        )

    def _points(self, job_id: str, start: Any, end: Any) -> "tuple[int, dict]":
        analysis = self._trace.analyse(job_id, start=start, end=end)
        # 非有限値の処理はここでは書かない（出口の `_json_safe` が木ごと通す）。
        return (
            200,
            {
                "ok": True,
                "job_id": job_id,
                "window": {"start": analysis.window[0], "end": analysis.window[1]},
                "rows": analysis.rows,
                # 防御的な写しを作らない（工程 5 再レビュー 🟡-B）。usecase が渡すのは
                # store が `to_list()` で作った素の list であり、出口の `_json_safe` は
                # **必ず新しい list を返す**。ここで `list(...)` を挟むと、捨てられるだけの
                # 中間リストが 1 本増える（実測: 20,000 行 × 7 列で一過性割当 +92.9%）。
                # 出力は 1 bit も変わらないので、状態検証では原理的に落ちない。
                "columns": {
                    name: analysis.columns[name] for name in ANALYSIS_COLUMNS
                },
                "events": [event.to_dict() for event in analysis.events],
                "drawdown": dict(analysis.drawdown),
                "time_unit": "epoch_millis",
            },
        )

    # --- 失敗の翻訳（値でなく状態で表す） ---------------------------------

    def _guarded(self, call) -> "tuple[int, dict]":
        """UC の失敗を HTTP の状態へ翻訳する。

        状態の割り当ては既存の `/data/{job_id}/{filename}` と揃える（同じ問いに
        2 つの答えを作らない）。翻訳表の実体は
        `simulator/sim_ui/adapter/json_api_translation.py` の 「`guarded`」 ただ 1 つであり、
        ここは本 API の 2 つの例外型を表へ渡すだけである:
            404 ジョブが無い / 成果物が無い / 識別子が受理形でない（存在を漏らさない）
            409 ジョブが完了していない（部分結果の非公開）
            413 窓に入る量が上限を超える（間引かずに断る）
            400 窓の指定そのものが不正
        """
        return guarded(
            call,
            missing=(TraceArtefactMissingError,),
            too_wide=(TraceWindowTooWideError,),
        )


def _bound(token: str) -> "int | None":
    """窓の片側を解く。`-` は無制限、それ以外は epoch ミリ秒の整数。

    解けない字は既定値で埋めない（§7）——埋めると「指定していない期間まで見えた」が
    静かに起きる。
    """
    if token == UNBOUNDED_TOKEN:
        return None
    try:
        return int(token)
    except ValueError:
        raise ValueError(
            f"窓の境界は epoch ミリ秒の整数か '{UNBOUNDED_TOKEN}' です: {token!r}"
        ) from None
