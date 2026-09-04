"""A-EaSeriesApiController: `GET /ea-series/{ea_name}` の HTTP 表現 ⇄ 系列カタログの変換。

責務（SRP）: **翻訳だけ**。パス末尾の ea_name を取り出し、系列カタログ Port
（:class:`IndicatorSeriesCatalogPort`）へ問い合わせ、その EA が実行に使う **registry 系列名**
（build_ea_indicators 単一ソース）を JSON で返す。系列名の決め方（factory 探索）は
adapter/ea_registry_series_catalog が単一ソースで持ち、ここには写さない。

なぜ必要か（名前空間結線・依頼者承認 2026-08-12）: 実行指示パネルの指標候補は、投入時の
受付検証（submit_job E-5）と GenericConditionStrategy が実際に参照する **ea_name 別の
registry 系列名**（ema / adx / close ...）と一致しなければならない。因果カタログの一覧
（`GET /indicators`）は別名前空間（MA / hl_range ...）かつ ea_name 非依存であり、それを
候補源にすると UI で選んだ指標が全て E-5 で 400 になる（実測済み）。本 API は front の候補源を
「選択 ea_name の registry 系列名」に一致させるための単一ソースの供給口である。

応答:
    200  {ok: true, ea_name, series: [...昇順...]}   決定的な昇順で返す
    400  {error}   ea_name セグメントがパスに無い（/ea-series・/ea-series/）

空集合（カタログが探索不能）でも 200 で返す（fail-open）。候補が空でもパネルは操作でき、
不整合な投入は受付検証（E-5）が受け皿として弾く。ここで 404/503 に倒すと「その EA には
指標が無い」と「探索できなかった」を利用者が区別できない。

JSON 直列化は既存 `job_api_controller.ApiResponse` を再利用する（同型の to_bytes を書かない）。
"""
from __future__ import annotations

from urllib.parse import unquote

from simulator.sim_ui.adapter.job_api_controller import ApiResponse
from simulator.sim_ui.usecase.job_ports import IndicatorSeriesCatalogPort

#: 系列一覧のパス接頭辞（sim core が受ける prefix 除去後の形）。
EA_SERIES_PATH = "/ea-series"


class EaSeriesApiController:
    """`GET /ea-series/{ea_name}` の入出力変換。"""

    def __init__(self, *, catalog: IndicatorSeriesCatalogPort) -> None:
        self._catalog = catalog

    @property
    def catalog(self) -> IndicatorSeriesCatalogPort:
        """系列の出所（合成根の検定が実物の結線を確かめるための面）。"""
        return self._catalog

    def list_for(self, path: str) -> ApiResponse:
        ea_name = _ea_name_of(path)
        if not ea_name:
            return ApiResponse(
                400,
                {"error": "ea_name（指標セット）をパスに含めてください: /ea-series/{ea_name}"},
            )
        series = sorted(self._catalog.series_for(ea_name))
        return ApiResponse(200, {"ok": True, "ea_name": ea_name, "series": series})


def _ea_name_of(path: str) -> str:
    """`/ea-series/{ea_name}` から ea_name を取り出す（URL デコード・前後スラッシュ除去）。"""
    rest = path[len(EA_SERIES_PATH):] if path.startswith(EA_SERIES_PATH) else path
    return unquote(rest.strip("/"))
