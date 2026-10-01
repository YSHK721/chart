"""``marketdata.quote_spread`` — 分内の気配幅（spread）規則の検定（ISSUE-511 段階 2）。

規則（依頼者裁定 2026-09-14）:
    spread = 分内 min((ask − bid) / point) を最近接整数へ丸めた int points。
    .5 ちょうどの同値は **偶数丸め**。真値が x.5 の気配幅は、浮動小数の表現誤差
    （例: (63044.95 − 63037.8) / 0.1 = 71.49999999994179）に左右されず偶数へ丸まる。

期待値はすべて書き下した整数である（実装をもう一度呼んで比べると、両側が同時に
間違ったときに気付けない）。point はテストが注入する値であり、銘柄仕様は読まない。
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from marketdata import quote_spread


def _quotes(rows: "list[tuple[str, float, float]]"):
    """``(分の時刻, bid, ask)`` から ``(bid, ask, minute)`` の 3 系列を作る。"""
    minute = pd.Series(pd.to_datetime([r[0] for r in rows]))
    bid = pd.Series([r[1] for r in rows], dtype="float64")
    ask = pd.Series([r[2] for r in rows], dtype="float64")
    return bid, ask, minute


# =====================================================================
# 分内最小
# =====================================================================

def test_the_spread_of_a_minute_is_the_smallest_width_in_points() -> None:
    """1 分内の複数ティックのうち、最も狭い気配幅を points で返す。"""
    # Arrange: 1 分に 3 ティック・幅 3.0 / 1.5 / 2.0・point=0.5
    bid, ask, minute = _quotes(
        [
            ("2026-09-01 00:00", 66000.0, 66003.0),
            ("2026-09-01 00:00", 66000.0, 66001.5),
            ("2026-09-01 00:00", 66000.0, 66002.0),
        ]
    )

    # Act
    got = quote_spread.minute_spread_points(bid, ask, minute, point=0.5)

    # Assert: 最小幅 1.5 / 0.5 = 3
    assert got.tolist() == [3]


def test_each_minute_gets_its_own_minimum_in_ascending_minute_order() -> None:
    """分ごとに独立に最小を取り、結果は分の昇順に並ぶ（入力順に依らない）。"""
    # Arrange: 分 1 を先に並べる（入力は分順ではない）。
    bid, ask, minute = _quotes(
        [
            ("2026-09-01 00:01", 66000.0, 66004.0),  # 分 1: 幅 4.0
            ("2026-09-01 00:00", 66000.0, 66002.0),  # 分 0: 幅 2.0
            ("2026-09-01 00:01", 66000.0, 66003.0),  # 分 1: 幅 3.0（最小）
            ("2026-09-01 00:00", 66000.0, 66001.0),  # 分 0: 幅 1.0（最小）
        ]
    )

    # Act
    got = quote_spread.minute_spread_points(bid, ask, minute, point=0.5)

    # Assert
    assert list(got.index) == [
        pd.Timestamp("2026-09-01 00:00"),
        pd.Timestamp("2026-09-01 00:01"),
    ]
    assert got.tolist() == [2, 6]


# =====================================================================
# 丸め（最近接・同値は偶数）
# =====================================================================

def test_widths_are_rounded_to_the_nearest_point() -> None:
    """幅 7.12 → 71・7.18 → 72（point=0.1・最近接整数）。"""
    # Arrange: 1 ティックずつの 2 分
    bid, ask, minute = _quotes(
        [
            ("2026-09-01 00:00", 63057.6, 63064.72),  # 71.2 points
            ("2026-09-01 00:01", 63057.6, 63064.78),  # 71.8 points
        ]
    )

    # Act
    got = quote_spread.minute_spread_points(bid, ask, minute, point=0.1)

    # Assert
    assert got.tolist() == [71, 72]


def test_rounding_is_neither_floor_nor_ceil() -> None:
    """70.4 → 70（ceil なら 71）・71.8 → 72（floor なら 71）で切捨て／切上げと区別する。"""
    # Arrange
    bid, ask, minute = _quotes(
        [
            ("2026-09-01 00:00", 66000.0, 66007.04),  # 70.4 points
            ("2026-09-01 00:01", 66000.0, 66007.18),  # 71.8 points
        ]
    )

    # Act
    got = quote_spread.minute_spread_points(bid, ask, minute, point=0.1)

    # Assert
    assert got.tolist() == [70, 72]


@pytest.mark.parametrize(
    ("bid", "ask", "true_points", "expected"),
    [
        (63037.8, 63044.95, 71.5, 72),  # 浮動小数では 71.49999999994179（素朴な round は 71）
        (63057.6, 63064.75, 71.5, 72),  # 浮動小数では 71.50000000001455
        (63037.6, 63044.65, 70.5, 70),  # 浮動小数では 70.5000000000291（素朴な round は 71）
        (32760.8, 32768.05, 72.5, 72),  # 浮動小数では 72.50000000003638（素朴な round は 73）
    ],
    ids=["71.5_below", "71.5_above", "70.5_above", "72.5_above"],
)
def test_a_width_of_exactly_half_a_point_rounds_to_even_regardless_of_float_error(
    bid, ask, true_points, expected
) -> None:
    """真値が x.5 の気配幅は偶数へ丸まる（依頼者裁定 2026-09-14）。

    入力は実際の価格同士の差で作り、浮動小数の表現誤差が**必ず入る**ものに限る
    （Arrange の前提表明で固定）。誤差の向きで丸めが変わるなら規則は「偶数丸め」ではない。
    """
    # Arrange: 前提＝この入力は浮動小数では真値 x.5 から外れている。
    assert (ask - bid) / 0.1 != true_points
    b, a, m = _quotes([("2026-09-01 00:00", bid, ask)])

    # Act
    got = quote_spread.minute_spread_points(b, a, m, point=0.1)

    # Assert
    assert got.tolist() == [expected]


def test_the_result_is_integer_points() -> None:
    """戻り値は int64（points は整数）。"""
    # Arrange
    bid, ask, minute = _quotes([("2026-09-01 00:00", 63057.6, 63064.72)])

    # Act
    got = quote_spread.minute_spread_points(bid, ask, minute, point=0.1)

    # Assert
    assert got.dtype == "int64"


# =====================================================================
# 異常系: point の検証
# =====================================================================

@pytest.mark.parametrize(
    "point",
    [0, -0.1, math.nan, math.inf, True, "0.1"],
    ids=["zero", "negative", "nan", "inf", "bool", "str"],
)
def test_an_invalid_point_is_refused(point) -> None:
    """0・負・NaN・inf・bool・非数値の point は :class:`ValueError`（黙って計算しない）。"""
    # Arrange
    bid, ask, minute = _quotes([("2026-09-01 00:00", 63057.6, 63064.72)])

    # Act / Assert
    with pytest.raises(ValueError):
        quote_spread.validate_point(point)
    with pytest.raises(ValueError):
        quote_spread.minute_spread_points(bid, ask, minute, point=point)


# =====================================================================
# 計算量: 丸め（points への換算）は分ごとに 1 回（R-3）
# =====================================================================

def _minutes_of_quotes(n_minutes: int, per_minute: int):
    """``n_minutes`` 分 × 1 分あたり ``per_minute`` 本の ``(bid, ask, minute)``。"""
    rows = []
    for m in range(n_minutes):
        for i in range(per_minute):
            bid = 66000.0 + i * 0.1
            rows.append((f"2026-09-01 00:{m:02d}", bid, bid + 7.0 + (i % 3) * 0.1))
    return _quotes(rows)


def test_widths_are_converted_to_points_once_per_minute_not_per_tick(monkeypatch) -> None:
    """CX（R-3）: points への換算（除算・丸め）の入力は分の数だけ（ティックごとに丸めない）。

    3 分 × 12 本と 3 分 × 120 本の 2 点で、換算の入力長合計 − 結果の分数 = 0、かつ
    入力長合計がティック数に依らないこと。
    """
    real = quote_spread._width_to_points
    results = []
    for per_minute in (12, 120):
        # Arrange
        converted: "list[int]" = []

        def spy(width, point):
            converted.append(len(width))
            return real(width, point)

        monkeypatch.setattr(quote_spread, "_width_to_points", spy)
        bid, ask, minute = _minutes_of_quotes(3, per_minute)

        # Act
        got = quote_spread.minute_spread_points(bid, ask, minute, point=0.1)

        # Assert
        assert len(got) > 0  # 空振り防止
        assert sum(converted) - len(got) == 0, (
            f"{per_minute} 本/分: {sum(converted)} 個の幅を換算し {len(got)} 分だけ使いました。"
        )
        results.append(sum(converted))

    assert results[0] == results[1], (
        f"1 分あたりのティックを 10 倍にしたら換算が {results[0]} → {results[1]} へ増えました。"
    )


_ROUNDING_CALLS = {"round", "rint", "around", "floor", "ceil", "trunc"}


def _conversion_nodes(source: str, owner: str) -> "tuple[set[int], list[str]]":
    """``owner`` 関数内のノード id 集合と、その外にある丸め呼出・``/`` の所在一覧を返す。"""
    import ast

    tree = ast.parse(source)
    inside: "set[int]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == owner:
            inside.update(id(n) for n in ast.walk(node))

    outside = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in _ROUNDING_CALLS and id(node) not in inside:
                outside.append(f"{name}() @ line {node.lineno}")
        elif isinstance(node, (ast.BinOp, ast.AugAssign)) and isinstance(node.op, ast.Div):
            if id(node) not in inside:
                outside.append(f"/ @ line {node.lineno}")
    return inside, outside


def test_rounding_and_point_division_live_only_in_the_points_conversion() -> None:
    """構造（R-3 の継ぎ目）: 丸め呼出と ``/`` は ``_width_to_points`` の中にしか無い。

    換算を ``_width_to_points`` の外（分 min より前など）へ書くと、上の Spy を迂回して
    ティックごとの丸めが入り込めるため、置き場所そのものを固定する。
    """
    from pathlib import Path

    # Arrange
    source = Path(quote_spread.__file__).read_text(encoding="utf-8")

    # Act
    inside, outside = _conversion_nodes(source, "_width_to_points")

    # Assert
    assert inside, "_width_to_points が定義されていません。"
    assert outside == [], f"_width_to_points の外に丸め・除算があります: {outside}"
