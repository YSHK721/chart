"""volume_step（保守側ロット丸め）の単体検定。

固定する不変条件（基本設計書 §12.2・依頼者裁定「丸めは保守側」）:
    1. volume_step の倍数へ **floor**（切り捨て）する。切り上げは resulting exposure を
       増やすため禁止（刻みを跨ぐケースは保守側＝少ない方へ丸める・§12.2）。
    2. volume_max を超えない（超える入力は volume_max 以下の最大の刻み倍数へ）。
    3. 丸めた結果が volume_min 未満なら **None**（発注不可）を返す。
       volume_min へ切り上げると「計算上許されない量」を建てることになり保守側でない。
    4. 浮動小数の刻み比誤差を吸収する（0.3/0.1 が 2.9999... になる類）。
       許容は Order._validate_volume と同じ土俵（刻み比の相対誤差）で判定する。

domain 単体（外部依存ゼロ・I/O なし）。
"""
from __future__ import annotations

import pytest

from simulator.domain.volume_step import floor_to_step


# --- 1. floor 丸め ---------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        (0.10, 0.10),   # 刻みちょうど
        (0.19, 0.10),   # 刻み未満の端数は捨てる
        (0.29, 0.20),
        (1.00, 1.00),
        (1.07, 1.00),
    ],
)
def test_刻みの倍数へ切り捨てる(raw: float, expected: float) -> None:
    # Arrange / Act
    got = floor_to_step(raw, step=0.10, minimum=0.01, maximum=100.0)
    # Assert
    assert got == pytest.approx(expected)


def test_切り上げは行わない() -> None:
    """§12.2「刻みを跨ぐケースは保守側（少ない方）へ丸める」。"""
    # Arrange / Act
    got = floor_to_step(0.199999, step=0.10, minimum=0.01, maximum=100.0)
    # Assert
    assert got == pytest.approx(0.10)


# --- 2. 上限 ---------------------------------------------------------------

def test_上限を超える入力は上限以下の最大刻み倍数になる() -> None:
    # Arrange / Act
    got = floor_to_step(999.0, step=0.10, minimum=0.01, maximum=5.05)
    # Assert
    assert got == pytest.approx(5.00)


def test_上限がちょうど刻みの倍数ならその値になる() -> None:
    got = floor_to_step(999.0, step=0.10, minimum=0.01, maximum=5.00)
    assert got == pytest.approx(5.00)


# --- 3. 下限（発注不可） ---------------------------------------------------

def test_丸め結果が下限未満なら発注不可() -> None:
    """下限へ切り上げると「計算上許されない量」を建てる＝保守側でない。"""
    # Arrange / Act
    got = floor_to_step(0.09, step=0.10, minimum=0.10, maximum=100.0)
    # Assert
    assert got is None


def test_下限ちょうどは発注可() -> None:
    got = floor_to_step(0.10, step=0.10, minimum=0.10, maximum=100.0)
    assert got == pytest.approx(0.10)


@pytest.mark.parametrize("raw", [0.0, -1.0])
def test_ゼロ以下は発注不可(raw: float) -> None:
    assert floor_to_step(raw, step=0.10, minimum=0.01, maximum=100.0) is None


# --- 4. 浮動小数誤差 -------------------------------------------------------

def test_刻み比の浮動小数誤差で1刻み落ちない() -> None:
    """0.3/0.1 は 2.9999999999999996。素の floor では 0.2 に落ちる。"""
    # Arrange / Act
    got = floor_to_step(0.3, step=0.1, minimum=0.01, maximum=100.0)
    # Assert
    assert got == pytest.approx(0.3)


def test_結果は刻みの倍数として表現できる() -> None:
    """Order._validate_volume（刻み比の丸め誤差許容 1e-6）を通る値であること。"""
    # Arrange
    step = 0.01
    # Act
    got = floor_to_step(1.2345, step=step, minimum=0.01, maximum=100.0)
    # Assert
    assert got is not None
    ratio = got / step
    assert abs(ratio - round(ratio)) < 1e-6


# --- 5. 引数の妥当性 -------------------------------------------------------

@pytest.mark.parametrize("step", [0.0, -0.1])
def test_刻みが正でなければ例外(step: float) -> None:
    with pytest.raises(ValueError):
        floor_to_step(1.0, step=step, minimum=0.01, maximum=100.0)


def test_上限が下限未満なら例外() -> None:
    with pytest.raises(ValueError):
        floor_to_step(1.0, step=0.1, minimum=1.0, maximum=0.5)
