"""A-EaSeriesApiController: GET /ea-series/{ea_name} の入出力変換の単体検定（Phase 6 F-8）.

固定する不変条件（名前空間結線の抜本解決・依頼者承認 2026-08-12）:
    1. パス末尾セグメントの ea_name に対し、系列カタログ Port の series_for をそのまま返す
       （registry 系列名の単一ソース＝EaRegistrySeriesCatalog）。
    2. 応答は 200・{ok, ea_name, series:[...]}。series は決定的に昇順ソート。
    3. ea_name セグメント不在（/ea-series・/ea-series/）は 400（何が足りないかを文言で返す）。
    4. カタログが空集合（探索不能）を返しても 200（fail-open・投入時 E-5 が受け皿）。
    5. ea_name は URL デコードして渡す。

方式: fake カタログ（IndicatorSeriesCatalogPort 実装）。実 registry 探索は統合検定が担う。
"""
from __future__ import annotations

from simulator.sim_ui.adapter.ea_series_api_controller import EaSeriesApiController
from simulator.sim_ui.usecase.job_ports import IndicatorSeriesCatalogPort


class _FakeCatalog(IndicatorSeriesCatalogPort):
    def __init__(self, table: "dict[str, frozenset[str]]") -> None:
        self._table = table
        self.asked: "list[str]" = []

    def series_for(self, ea_name: str) -> "frozenset[str]":
        self.asked.append(ea_name)
        return self._table.get(ea_name, frozenset())


def _controller(table):
    return EaSeriesApiController(catalog=_FakeCatalog(table))


def test_known_ea_returns_200_with_series() -> None:
    # Arrange
    ctrl = _controller({"PRO_fit_Band_EA": frozenset({"ema", "adx", "close"})})
    # Act
    resp = ctrl.list_for("/ea-series/PRO_fit_Band_EA")
    # Assert
    assert resp.status == 200
    assert resp.payload["ok"] is True
    assert resp.payload["ea_name"] == "PRO_fit_Band_EA"
    assert resp.payload["series"] == ["adx", "close", "ema"]  # 昇順で決定的


def test_series_are_sorted_deterministically() -> None:
    ctrl = _controller({"X": frozenset({"z", "a", "m"})})
    resp = ctrl.list_for("/ea-series/X")
    assert resp.payload["series"] == ["a", "m", "z"]


def test_missing_ea_name_segment_is_400() -> None:
    ctrl = _controller({"X": frozenset({"a"})})
    for path in ("/ea-series", "/ea-series/"):
        resp = ctrl.list_for(path)
        assert resp.status == 400, path
        assert "error" in resp.payload


def test_empty_series_is_200_fail_open() -> None:
    # カタログが空集合（探索不能）でも 200（投入時 E-5 が受け皿・無音にしない）
    ctrl = _controller({})
    resp = ctrl.list_for("/ea-series/UnknownEA")
    assert resp.status == 200
    assert resp.payload["series"] == []


def test_ea_name_is_url_decoded() -> None:
    cat = _FakeCatalog({"MA Slope": frozenset({"ema"})})
    ctrl = EaSeriesApiController(catalog=cat)
    resp = ctrl.list_for("/ea-series/MA%20Slope")
    assert resp.status == 200
    assert cat.asked == ["MA Slope"]
    assert resp.payload["series"] == ["ema"]


def test_catalog_exposed_for_composition_check() -> None:
    cat = _FakeCatalog({"X": frozenset({"a"})})
    ctrl = EaSeriesApiController(catalog=cat)
    assert ctrl.catalog is cat
