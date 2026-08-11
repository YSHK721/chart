"""EaRegistrySeriesCatalog（E-3 判定の系列カタログ・adapter）の結合検定。

§12.5 の判定材料「戦略の指標レジストリがどの系列を持つか」を、**`simulator.main` の
`_EA_FACTORIES` を単一ソースとして実際に呼んで**得ることを固定する。
§12.1「戦略ごとの明示指定リストはハードコードとして実装しない」に従い、
ea_name → 系列名の対応表をこちらで書き写さない（書き写すと必ず腐る）。

固定する不変条件:
    1. 実測どおりの系列集合が返る（`simulator/main/__init__.py` の registry ビルダ）。
       - MA_Slope_EA        : {"ema"} のみ ＝ 価格系列なし ＝ E-3 の拒否対象
       - MA_Slope_Pending_EA: ema / open / spread
       - StopEntryProbe_EA  : ema / open / spread
       - WeeklyVolBand_EA   : open
       - PRO_fit_Band_EA    : ema / adx / plus_di / minus_di / close
    2. 未登録の ea_name は `build_interactor` と同じく既定 TC 経路へフォールバックする
       （本番の振る舞いと食い違わせない）。
    3. 探索に失敗した場合は **fail-safe に空集合**（＝sizing 不可）を返す。
       黙って通して誤った発注量で走らせるより、拒否して気付かせる。
    4. 登録表（`_EA_FACTORIES`）の全エントリを解決できる（表が増えても落ちない）。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.adapter.ea_registry_series_catalog import EaRegistrySeriesCatalog


@pytest.fixture(scope="module")
def catalog() -> EaRegistrySeriesCatalog:
    return EaRegistrySeriesCatalog()


@pytest.mark.parametrize(
    "ea_name, expected",
    [
        ("MA_Slope_EA", {"ema"}),
        ("MA_Slope_Pending_EA", {"ema", "open", "spread"}),
        ("StopEntryProbe_EA", {"ema", "open", "spread"}),
        ("WeeklyVolBand_EA", {"open"}),
        ("PRO_fit_Band_EA", {"ema", "adx", "plus_di", "minus_di", "close"}),
    ],
)
def test_登録系列は実測どおり(
    catalog: EaRegistrySeriesCatalog, ea_name: str, expected: set
) -> None:
    # Arrange / Act
    got = catalog.series_for(ea_name)
    # Assert
    assert set(got) == expected


def test_MA_Slope_EAは価格系列を持たない(catalog: EaRegistrySeriesCatalog) -> None:
    """§12.5 の拒否対象が実在することの直接の実証（E-3 の存在理由）。"""
    # Arrange / Act
    got = catalog.series_for("MA_Slope_EA")
    # Assert
    assert "close" not in got
    assert "open" not in got


def test_未登録のea_nameは既定TC経路へフォールバックする(
    catalog: EaRegistrySeriesCatalog,
) -> None:
    """`build_interactor` は未登録 ea_name を `_factory_tc24051901` へ倒す（実測）。

    カタログが本番と違う判定をすると、通るはずのジョブを拒む／拒むべきを通す。
    """
    # Arrange / Act
    got = catalog.series_for("No_Such_EA")
    # Assert
    assert set(got) == {"madiff", "close"}


def test_登録表の全エントリを解決できる(catalog: EaRegistrySeriesCatalog) -> None:
    """`_EA_FACTORIES` にエントリが増えても、この検定が先に落ちて気付ける。"""
    # Arrange
    from simulator.main import _EA_FACTORIES

    # Act / Assert
    for ea_name in _EA_FACTORIES:
        assert catalog.series_for(ea_name), f"{ea_name} の系列を解決できない"


def test_探索に失敗したら空集合を返す(monkeypatch) -> None:
    """fail-safe（§12.5 の趣旨: 無音の誤動作を作らない）。"""
    # Arrange
    catalog = EaRegistrySeriesCatalog()

    def _boom(*a, **k):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(catalog, "_probe", _boom)
    # Act
    got = catalog.series_for("PRO_fit_Band_EA")
    # Assert
    assert got == frozenset()


def test_同一ea_nameの再問い合わせは探索を繰り返さない() -> None:
    """受付は 1 秒以内（§8.1 Phase 2 通過条件 2）。毎回 pandas を回さない。"""
    # Arrange
    catalog = EaRegistrySeriesCatalog()
    calls = []
    original = catalog._probe

    def _counted(ea_name: str):
        calls.append(ea_name)
        return original(ea_name)

    catalog._probe = _counted  # type: ignore[method-assign]
    # Act
    catalog.series_for("PRO_fit_Band_EA")
    catalog.series_for("PRO_fit_Band_EA")
    # Assert
    assert calls == ["PRO_fit_Band_EA"]
