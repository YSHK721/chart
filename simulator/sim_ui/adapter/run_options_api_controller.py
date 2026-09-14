"""A-RunOptionsApiController: `GET /run-options` の HTTP 表現 ⇄ 選択肢 UC の変換（Phase 6 拡張）。

責務（SRP）: **翻訳だけ**。ListRunOptionsInteractor から選択肢（データセット＋ea_name）を得て
JSON で返す。銘柄仕様・データセットの決め方は adapter/symbol_spec_catalog が単一ソースで持ち、
ここには写さない（ea_series_api_controller と同型）。

応答:
    200  {ok: true, datasets: [{dataset, data_path, symbol, period, ...銘柄仕様8...}], ea_names: [...]}

JSON 直列化は既存 `job_api_controller.ApiResponse` を再利用する（同型の to_bytes を書かない）。
"""
from __future__ import annotations

from simulator.sim_ui.adapter.job_api_controller import ApiResponse
from simulator.sim_ui.usecase.list_run_options import ListRunOptionsInteractor

#: 選択肢一覧のパス（sim core が受ける prefix 除去後の形）。
RUN_OPTIONS_PATH = "/run-options"


class RunOptionsApiController:
    """`GET /run-options` の入出力変換。"""

    def __init__(self, *, options: ListRunOptionsInteractor) -> None:
        self._options = options

    @property
    def options(self) -> ListRunOptionsInteractor:
        """選択肢 UC（合成根の検定が実物の結線を確かめるための面）。"""
        return self._options

    def list(self) -> ApiResponse:
        result = self._options.list()
        return ApiResponse(
            200,
            {
                "ok": True,
                "datasets": [p.to_dict() for p in result.datasets],
                "ea_names": list(result.ea_names),
            },
        )
