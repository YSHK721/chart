"""ライブ compute 系列 → 指標レジストリ供給（adapter）の単体検定。

固定する規則（Phase 3 構造設計 §新規ファイル #7・契約改訂裁定 A）:
    1. 系列は `CausalSeriesProbePort` の**束**で受け取る（1 指標 1 回の計算）。
       案 ii の窓は供給窓の末尾を until とする（prefix 関係を保つ）。
    2. 整列は usecase の `align_series_to_bars` に委ねる（規則の写しを持たない）。
    3. **レジストリへ載せるのは台帳が選択可能とした系列だけ**。未検定の系列を混ぜると
       戦略がその値を掴んだまま完走する。
    4. 系列名の衝突は `SeriesNameCollisionError`（fail-closed）。
    5. 整列規則の違反（有効区間の欠測・時間軸不一致）は `SeriesAlignmentError` のまま
       上へ通す。`IndicatorNaNError` に化けさせない。
    6. **新規 `IndicatorPort` 実装クラスは作らない**（既存 `PandasIndicatorRegistry`）。
    7. 指標式の再実装はしない。値は束の値をそのまま運ぶ。

方式: 合成の `FakeCausalSeriesProbe` のみ。indicator_ui も実データも触らない。
"""
from __future__ import annotations

import math

import pytest

from simulator.sim_ui.adapter.live_indicator_supply import LiveIndicatorSupply
from simulator.sim_ui.tests.integration._fake_indicator_ports import (
    FakeCausalSeriesProbe,
)
from simulator.sim_ui.usecase.indicator_models import (
    IndicatorSpec,
    IndicatorSupplyError,
    SeriesAlignmentError,
    SeriesBundle,
    SeriesNameCollisionError,
    SeriesPoint,
)

_BARS = [100, 160, 220]
_MA = IndicatorSpec(indicator="moving_averages", variant="default", params={})


def _supply(full) -> LiveIndicatorSupply:
    return LiveIndicatorSupply(probe=FakeCausalSeriesProbe(full=full))


def _bundle(**series) -> SeriesBundle:
    return SeriesBundle(
        {name: [SeriesPoint(time=t, value=v) for t, v in points]
         for name, points in series.items()}
    )


# --- 1. 整列（規則 1・2）--------------------------------------------------

def test_系列がバー時刻列へ整列される() -> None:
    # Arrange
    supply = _supply({"MA": [(100, 1.0), (160, 2.0), (220, 3.0)]})
    # Act
    values = supply.series_values(_MA, ref="jp225", timeframe="5m", bar_times=_BARS)
    # Assert
    assert values == {"MA": [1.0, 2.0, 3.0]}


def test_供給窓の末尾をuntilにして計算する() -> None:
    """規則 1（prefix 関係）。窓の右端を案 i と揃える。"""
    # Arrange
    probe = FakeCausalSeriesProbe(full={"MA": [(100, 1.0), (160, 2.0), (220, 3.0)]})
    supply = LiveIndicatorSupply(probe=probe)
    # Act
    supply.series_values(_MA, ref="jp225", timeframe="5m", bar_times=[100, 160])
    # Assert
    assert probe.full_calls == [160]


def test_先頭warmupはNaNで埋まる() -> None:
    # Arrange
    supply = _supply({"MA": [(220, 3.0)]})
    # Act
    values = supply.series_values(_MA, ref="jp225", timeframe="5m", bar_times=_BARS)
    # Assert
    assert math.isnan(values["MA"][0]) and math.isnan(values["MA"][1])
    assert values["MA"][2] == 3.0


def test_未定義値の点はNaNとして運ぶ() -> None:
    """点はある（欠測ではない）。破損判定はレジストリの責務。"""
    # Arrange
    supply = _supply({"MA": [(100, None), (160, 2.0), (220, 3.0)]})
    # Act
    values = supply.series_values(_MA, ref="jp225", timeframe="5m", bar_times=_BARS)
    # Assert
    assert math.isnan(values["MA"][0])
    assert values["MA"][1:] == [2.0, 3.0]


def test_計算済みの束をそのまま整列できる() -> None:
    """段 0 で測った束を測り直さない（供給コストを二重に払わない）。"""
    # Arrange
    bundle = _bundle(MA=[(100, 1.0), (160, 2.0), (220, 3.0)])
    # Act
    values = LiveIndicatorSupply.align_bundle(bundle, bar_times=_BARS)
    # Assert
    assert values == {"MA": [1.0, 2.0, 3.0]}


def test_空のバー列は空になる() -> None:
    """境界値: 供給窓 0 本。"""
    # Arrange
    probe = FakeCausalSeriesProbe(full={"MA": [(100, 1.0)]})
    supply = LiveIndicatorSupply(probe=probe)
    # Act
    values = supply.series_values(_MA, ref="jp225", timeframe="5m", bar_times=[])
    # Assert
    assert values == {}
    assert probe.full_calls == []   # 計算しに行かない


# --- 2. レジストリ構築（規則 3・6）----------------------------------------

def test_既存のPandasIndicatorRegistryを構築する() -> None:
    # Arrange
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    supply = _supply({"MA": [(100, 1.0), (160, 2.0), (220, 3.0)]})
    # Act
    registry = supply.build_registry(
        specs=[_MA], ref="jp225", timeframe="5m", bar_times=_BARS, selectable={"MA"}
    )
    # Assert
    assert isinstance(registry, PandasIndicatorRegistry)
    series = registry.get("MA")
    assert list(series.index) == [0, 1, 2]
    assert list(series) == [1.0, 2.0, 3.0]


def test_選択可能でない系列はレジストリに載らない() -> None:
    """規則 3。検定を通っていない系列を戦略へ渡さない。"""
    # Arrange
    supply = _supply({
        "MA": [(100, 1.0), (160, 2.0), (220, 3.0)],
        "UNVERIFIED": [(100, 9.0), (160, 9.0), (220, 9.0)],
    })
    # Act
    registry = supply.build_registry(
        specs=[_MA], ref="jp225", timeframe="5m", bar_times=_BARS, selectable={"MA"}
    )
    # Assert
    assert registry.get("MA") is not None
    with pytest.raises(Exception):
        registry.get("UNVERIFIED")


def test_選択可能な系列が1つも無ければ明示エラー() -> None:
    """境界値: 全系列が未検定。黙って空のレジストリを返さない。"""
    # Arrange
    supply = _supply({"MA": [(100, 1.0), (160, 2.0), (220, 3.0)]})
    # Act / Assert
    with pytest.raises(IndicatorSupplyError) as exc:
        supply.build_registry(
            specs=[_MA], ref="jp225", timeframe="5m", bar_times=_BARS, selectable=set()
        )
    assert "moving_averages" in str(exc.value)


def test_単一バーでもレジストリを作れる() -> None:
    """境界値: バー 1 本。"""
    # Arrange
    supply = _supply({"MA": [(100, 1.0)]})
    # Act
    registry = supply.build_registry(
        specs=[_MA], ref="jp225", timeframe="5m", bar_times=[100], selectable={"MA"}
    )
    # Assert
    assert list(registry.get("MA")) == [1.0]


# --- 3. fail-closed（規則 4・5）-------------------------------------------

def test_指標をまたぐ系列名の衝突は明示エラー() -> None:
    """後勝ちで黙って上書きしない（戦略が別指標の値を掴む）。"""
    # Arrange（別 spec でも同じ束を返すフェイク＝同名系列）
    other = IndicatorSpec(indicator="profit_oscillator", variant="default", params={})
    supply = _supply({"MA": [(100, 1.0), (160, 2.0), (220, 3.0)]})
    # Act / Assert
    with pytest.raises(SeriesNameCollisionError) as exc:
        supply.build_registry(
            specs=[_MA, other], ref="jp225", timeframe="5m", bar_times=_BARS,
            selectable={"MA"},
        )
    assert "MA" in str(exc.value)


def test_有効区間の欠測は整列エラーのまま上へ通す() -> None:
    """規則 5。`IndicatorNaNError` に化けさせない（原因の語彙をすり替えない）。"""
    # Arrange（160 の点が無い）
    supply = _supply({"MA": [(100, 1.0), (220, 3.0)]})
    # Act / Assert
    with pytest.raises(SeriesAlignmentError):
        supply.series_values(_MA, ref="jp225", timeframe="5m", bar_times=_BARS)


def test_バー時刻に無い点は整列エラーになる() -> None:
    """時間軸不一致（別の足で計算された系列）を無音で受け入れない。"""
    # Arrange
    supply = _supply({"MA": [(100, 1.0), (130, 1.5), (160, 2.0), (220, 3.0)]})
    # Act / Assert
    with pytest.raises(SeriesAlignmentError):
        supply.series_values(_MA, ref="jp225", timeframe="5m", bar_times=_BARS)
