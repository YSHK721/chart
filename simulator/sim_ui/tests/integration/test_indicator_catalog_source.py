"""検定対象母集合の導出（`IndicatorCatalogSourcePort` 実装・adapter）の結合検定。

固定する不変条件（Phase 3 構造設計 §新規ファイル #9・§絶対制約「手書き複製禁止」）:
    1. 母集合は **ライブの `GET /catalog`（`handle_catalog`）が唯一の情報源**。
       指標名の手書きリストを持たない（増えた指標が黙って検定対象から漏れる）。
    2. variant は `paramScopes` の鍵をそのまま使う（variant ごとに受理 param が違う）。
    3. params は当該 variant が受理する param の既定値だけを載せる。
       受理しない param を送ると compute が validation エラーになる（ISSUE-278 #8）。
    4. catalog を取得できないときは明示エラー（空の母集合＝「検定対象なし」に倒さない）。
    5. 並びは (indicator, variant) 昇順で決定的にする。

方式: 本物の `handle_catalog`（read-only・実データ不要）と、異常系のフェイク handler。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.adapter.indicator_catalog_source import IndicatorCatalogSource
from simulator.sim_ui.usecase.indicator_models import IndicatorCatalogUnavailableError


def _real_catalog() -> "tuple[dict, dict]":
    """検定側でも**同じ単一情報源**から期待値を作る（手書きの表を作らない）。"""
    from simulator.replay_ui.adapter import _indicator_ui_bridge

    status, body = _indicator_ui_bridge.load_catalog_handler().handle_catalog()
    assert status == 200 and body.get("ok") is True
    return body["catalog"], body["paramScopes"]


# --- 1. 母集合（不変条件 1・2）--------------------------------------------

def test_母集合はライブのcatalogから導出される() -> None:
    # Arrange
    catalog, scopes = _real_catalog()
    expected = sorted(
        (compute_id, variant)
        for compute_id, variants in scopes.items()
        for variant in variants
    )
    # Act
    specs = IndicatorCatalogSource().specs()
    # Assert
    assert [(s.indicator, s.variant) for s in specs] == expected
    assert len(specs) >= len(catalog), "指標が黙って母集合から漏れている"


def test_variantごとに別の申告になる() -> None:
    """`profit_band` は global / robust の 2 variant を持つ（実測）。"""
    # Arrange / Act
    specs = IndicatorCatalogSource().specs()
    # Assert
    variants = sorted(s.variant for s in specs if s.indicator == "profit_band")
    assert variants == ["global", "robust"]


# --- 2. params（不変条件 3）-----------------------------------------------

def test_paramsは当該variantが受理するものだけ() -> None:
    # Arrange
    catalog, scopes = _real_catalog()
    # Act
    specs = {(s.indicator, s.variant): s for s in IndicatorCatalogSource().specs()}
    # Assert
    for (compute_id, variant), spec in specs.items():
        accepted = set(scopes[compute_id][variant])
        assert set(spec.params) <= accepted, f"{compute_id}/{variant} が受理しない param"


def test_paramsの値はcatalogの既定値() -> None:
    # Arrange
    catalog, _scopes = _real_catalog()
    # Act
    specs = {(s.indicator, s.variant): s for s in IndicatorCatalogSource().specs()}
    spec = specs[("moving_averages", "default")]
    # Assert
    for name, value in spec.params.items():
        assert value == catalog["moving_averages"][name]


def test_母集合の単位は指標とvariant() -> None:
    """母集合の時点では出力系列が分からない（1 回の計算＝束が実際の系列を教える）。

    系列名は `IndicatorSpec` の一部ではない（契約改訂裁定 A: 系列は束で運ぶ）。
    """
    # Arrange / Act
    specs = IndicatorCatalogSource().specs()
    # Assert
    assert not hasattr(specs[0], "series_name")
    assert len({(s.indicator, s.variant) for s in specs}) == len(specs)


# --- 3. fail-closed（不変条件 4）------------------------------------------

class _FailingHandler:
    def __init__(self, status: int, body: dict) -> None:
        self._status = status
        self._body = body

    def __call__(self) -> "tuple[int, dict]":
        return self._status, self._body


@pytest.mark.parametrize("status, body", [
    (500, {"error": {"kind": "internal"}}),
    (200, {"ok": False}),
    (200, {"ok": True}),                      # catalog / paramScopes 欠落
    (200, {"ok": True, "catalog": {}, "paramScopes": None}),
])
def test_catalogを取得できなければ明示エラー(status: int, body: dict) -> None:
    """空の母集合＝「検定対象なし」に倒さない（検定が黙って空振りする）。"""
    # Arrange
    source = IndicatorCatalogSource(catalog_handler=_FailingHandler(status, body))
    # Act / Assert
    with pytest.raises(IndicatorCatalogUnavailableError):
        source.specs()


# --- 4. 決定的な並び（不変条件 5）-----------------------------------------

def test_並び順はindicatorとvariantの昇順() -> None:
    # Arrange
    source = IndicatorCatalogSource(catalog_handler=_FailingHandler(200, {
        "ok": True,
        "catalog": {"b_ind": {"x": 1}, "a_ind": {"y": 2}},
        "paramScopes": {"b_ind": {"default": ["x"]},
                        "a_ind": {"robust": ["y"], "global": ["y"]}},
    }))
    # Act
    specs = source.specs()
    # Assert
    assert [(s.indicator, s.variant) for s in specs] == [
        ("a_ind", "global"), ("a_ind", "robust"), ("b_ind", "default")
    ]
