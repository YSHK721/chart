"""align_series_to_bars（系列点列 → バー時刻列の整列・usecase）の単体検定。

固定する規則（Phase 3 構造設計 §新規ファイル #4）:
    1. バー時刻列と同じ長さ・同じ順の値列を返す（index は呼び出し側が付ける）。
    2. **先頭 warmup の欠測は許容**する（値は NaN）。指標の未定義区間であり破損ではない。
    3. **有効区間（最初に点が現れたバー以降）の欠測は明示エラー**（`SeriesAlignmentError`）。
       ここを NaN で埋めると `PandasIndicatorRegistry` の `IndicatorNaNError` に化け、
       「どこで壊れたか」が指標レジストリの語彙にすり替わる（原因が消える）。
    4. **バー時刻列に無い時刻の点があれば時間軸不一致**として明示エラー。無音で捨てない。
    5. 点が持つ値そのものの NaN は破損判定をしない（warmup NaN は指標側の正常出力）。

方式: 合成データのみ（Port も pandas も使わない純関数）。
"""
from __future__ import annotations

import math

import pytest

from simulator.sim_ui.usecase.align_series_to_bars import align_series_to_bars
from simulator.sim_ui.usecase.indicator_models import SeriesPoint, SeriesAlignmentError


def _points(pairs) -> "list[SeriesPoint]":
    return [SeriesPoint(time=t, value=v) for t, v in pairs]


# --- 1. 正常系 -------------------------------------------------------------

def test_全バーに点があれば同順の値列になる() -> None:
    # Arrange
    bars = [100, 160, 220]
    points = _points([(100, 1.0), (160, 2.0), (220, 3.0)])
    # Act
    values = align_series_to_bars(points, bars)
    # Assert
    assert values == [1.0, 2.0, 3.0]


def test_単一バーでも整列できる() -> None:
    """境界値: バー 1 本。"""
    # Arrange
    bars = [100]
    points = _points([(100, 7.5)])
    # Act
    values = align_series_to_bars(points, bars)
    # Assert
    assert values == [7.5]


# --- 2. warmup 欠測の許容（境界値: 先頭） ----------------------------------

def test_先頭のwarmup欠測はNaNで許容される() -> None:
    # Arrange
    bars = [100, 160, 220, 280]
    points = _points([(220, 3.0), (280, 4.0)])
    # Act
    values = align_series_to_bars(points, bars)
    # Assert
    assert math.isnan(values[0]) and math.isnan(values[1])
    assert values[2:] == [3.0, 4.0]


def test_空系列は全バーがNaNになる() -> None:
    """境界値: 空系列。有効区間が始まらないので全区間が warmup。"""
    # Arrange
    bars = [100, 160]
    # Act
    values = align_series_to_bars([], bars)
    # Assert
    assert len(values) == 2
    assert all(math.isnan(v) for v in values)


def test_空バー列かつ空系列は空リスト() -> None:
    """境界値: 0 本。"""
    assert align_series_to_bars([], []) == []


# --- 3. 有効区間内の欠測は明示エラー ---------------------------------------

def test_有効区間の途中欠測は明示エラー() -> None:
    # Arrange（160 の点だけが無い＝warmup ではない）
    bars = [100, 160, 220]
    points = _points([(100, 1.0), (220, 3.0)])
    # Act / Assert
    with pytest.raises(SeriesAlignmentError) as exc:
        align_series_to_bars(points, bars)
    assert "160" in str(exc.value)


def test_有効区間の末尾欠測も明示エラー() -> None:
    # Arrange
    bars = [100, 160, 220]
    points = _points([(100, 1.0), (160, 2.0)])
    # Act / Assert
    with pytest.raises(SeriesAlignmentError):
        align_series_to_bars(points, bars)


# --- 4. 時間軸不一致 -------------------------------------------------------

def test_窓の内側でバー時刻に無い点があれば時間軸不一致で明示エラー() -> None:
    """規則 4。別の足で計算された系列を無音で受け入れない。"""
    # Arrange（130 は窓 [100, 160] の内側だがバー時刻に無い）
    bars = [100, 160]
    points = _points([(100, 1.0), (130, 1.5), (160, 2.0)])
    # Act / Assert
    with pytest.raises(SeriesAlignmentError) as exc:
        align_series_to_bars(points, bars)
    assert "130" in str(exc.value)


# --- 4b. 窓の外側の点（実測で判明した規則・2026-08-11）--------------------
#
# 実測された壊れ方: 案 ii は**データセット全期間**（実測 50,000 バー）を 1 回計算する
# 一方、検定・供給が対象にするのはその一部（末尾 N 本）である。窓の外側の点まで
# 「バー時刻列に無い＝時間軸不一致」と判定すると、実データでは **必ず** 整列に失敗し、
# 全指標が「供給できません」で選択不可になる（＝検定が常に空振りする）。
#
# 窓の外（範囲外）と、窓の中のグリッド不一致は別事象である。前者は「対象外」、
# 後者は「別の足で計算された系列」。前者まで弾いていたのが原因であり、
# 後者の防御（上の検定）は保ったまま前者だけを対象外にする。

def test_窓より前の点は対象外として落とす() -> None:
    # Arrange
    bars = [160, 220]
    points = _points([(40, 0.4), (100, 1.0), (160, 2.0), (220, 3.0)])
    # Act
    values = align_series_to_bars(points, bars)
    # Assert
    assert values == [2.0, 3.0]


def test_窓より後の点は対象外として落とす() -> None:
    # Arrange
    bars = [100, 160]
    points = _points([(100, 1.0), (160, 2.0), (220, 3.0), (280, 4.0)])
    # Act
    values = align_series_to_bars(points, bars)
    # Assert
    assert values == [1.0, 2.0]


def test_窓の外側の点はwarmup判定に影響しない() -> None:
    """窓より前に点があっても、窓の先頭の欠測は warmup として許容する。"""
    # Arrange（窓は [160, 220]。160 の点は無い）
    bars = [160, 220]
    points = _points([(100, 1.0), (220, 3.0)])
    # Act
    values = align_series_to_bars(points, bars)
    # Assert
    assert math.isnan(values[0])
    assert values[1] == 3.0


def test_バー列が空なら窓が空なので空リスト() -> None:
    """境界値: バー 0 本 + 点あり。窓が空＝対象 0 本（比較しない）。

    fail-closed は整列ではなく検定側が担う（比較 0 本は「一致」と呼ばない）。
    """
    assert align_series_to_bars(_points([(100, 1.0)]), []) == []


# --- 5. 未定義値は破損判定しない ------------------------------------------

def test_点の値が未定義でも欠測とは扱わない() -> None:
    """指標が warmup 区間に未定義値を出すのは正常出力。整列は NaN として運ぶ。

    「点そのものが無い」（有効区間の欠測＝エラー）とは別事象である。
    """
    # Arrange
    bars = [100, 160, 220]
    points = _points([(100, None), (160, None), (220, 3.0)])
    # Act
    values = align_series_to_bars(points, bars)
    # Assert
    assert math.isnan(values[0]) and math.isnan(values[1])
    assert values[2] == 3.0


def test_全点が未定義値でもエラーにしない() -> None:
    """境界値: 全点が未定義。破損判定は指標レジストリの責務であり整列の責務ではない。"""
    # Arrange
    bars = [100, 160]
    points = _points([(100, None), (160, None)])
    # Act
    values = align_series_to_bars(points, bars)
    # Assert
    assert len(values) == 2
    assert all(math.isnan(v) for v in values)
