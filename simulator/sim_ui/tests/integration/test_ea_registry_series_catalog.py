"""EaRegistrySeriesCatalog（E-3 判定の系列カタログ・adapter）の結合検定。

§12.5 の判定材料「戦略の指標レジストリがどの系列を持つか」を、**`simulator.main` の
公開アクセサ（`build_ea_indicators`）を単一ソースとして実際に呼んで**得ることを固定する。
束縛は Composition Root が持つ（ISSUE-405・R-4 と同型）。
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
    4. 実行可能な全 EA 名（`known_ea_names()`）を解決できる（表が増えても落ちない）。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.adapter.ea_build_probe import EaBuildProbe
from simulator.sim_ui.adapter.ea_registry_series_catalog import EaRegistrySeriesCatalog
from simulator.sim_ui.main.composition_root_jobs import build_series_catalog


@pytest.fixture(scope="module")
def catalog() -> EaRegistrySeriesCatalog:
    # 束縛は Composition Root から取る（テストが束縛先を書き写さない）。
    return build_series_catalog()


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
    """`build_interactor` は未登録 ea_name を既定 TC 経路へ倒す（実測）。

    カタログが本番と違う判定をすると、通るはずのジョブを拒む／拒むべきを通す。
    """
    # Arrange / Act
    got = catalog.series_for("No_Such_EA")
    # Assert
    assert set(got) == {"madiff", "close"}


def test_実行可能な全EAを解決できる(catalog: EaRegistrySeriesCatalog) -> None:
    """登録表にエントリが増えても、この検定が先に落ちて気付ける。

    一覧の出所は `simulator.main.known_ea_names`（私有な登録表を覗かない・ISSUE-405）。
    """
    # Arrange
    from simulator.main import known_ea_names

    # Act / Assert
    for ea_name in known_ea_names():
        assert catalog.series_for(ea_name), f"{ea_name} の系列を解決できない"


def test_探索に失敗したら空集合を返す() -> None:
    """fail-safe（§12.5 の趣旨: 無音の誤動作を作らない）。

    私有メソッドを monkeypatch せず、**注入で**構築を失敗させる（束縛の差し替え点が
    実在することの確認でもある）。
    """
    # Arrange
    def _cannot_build(**_spec):
        raise RuntimeError("probe failed")

    catalog = EaRegistrySeriesCatalog(probe=EaBuildProbe(_cannot_build))
    # Act
    got = catalog.series_for("PRO_fit_Band_EA")
    # Assert
    assert got == frozenset()


def test_同一ea_nameの再問い合わせは探索を繰り返さない() -> None:
    """受付は 1 秒以内（§8.1 Phase 2 通過条件 2）。毎回 pandas を回さない。"""
    # Arrange
    from simulator.sim_ui.main.composition_root_jobs import _build_ea_indicators

    calls: "list[str]" = []

    def _counted(**spec):
        calls.append(spec["ea_name"])
        return _build_ea_indicators(**spec)

    catalog = EaRegistrySeriesCatalog(probe=EaBuildProbe(_counted))
    # Act
    catalog.series_for("PRO_fit_Band_EA")
    after_first = len(calls)
    catalog.series_for("PRO_fit_Band_EA")
    # Assert（初回は形式フォールバックで 2 回組み立てを試す。2 回目は 0 回）
    assert after_first >= 1
    assert len(calls) == after_first


def test_既定束縛を持たない() -> None:
    """R-4 と同型: 構築の束縛は必須引数（adapter → main の外向き依存を作らない）。"""
    import inspect

    parameter = inspect.signature(EaRegistrySeriesCatalog.__init__).parameters["probe"]
    assert parameter.default is inspect.Parameter.empty
