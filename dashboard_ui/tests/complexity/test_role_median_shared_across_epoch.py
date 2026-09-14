"""計算量 12（ISSUE-464 ④）: 役割判定の中央値は確定ぶんを epoch 単位で持ち越す。

§3.1 の役割判定（水準か否か）は系列**全点**の中央値を実値の帯（現在値の 0.3〜3 倍）へ
当てる。全点のうち動くのは形成中バーの 1 点だけであり、確定ぶんは epoch の中で不変である。
にもかかわらず系列 1 本ごとに全点を毎要求走査し直していた（実測 2026-08-30・8 足束
1 要求: 224 本 / 342 ms）。

出力は正しいままなので状態検証では原理的に落ちない（ISSUE-450 / ISSUE-257 と同型）。
CLAUDE.md 絶対命令 §4.1 に従い、測るのは時間ではなく**回数**である。回数そのものは
期待値に焼き込まない。固定するのは次の 5 つだけである。

- **不変量 A（要求で増えない）**: 素材が変わらない限り、要求を N 回繰り返しても
  確定ぶんの走査の**追加は 0**。N は 2 点（5 / 20）で固定する。
- **不変量 B（鮮度と両立）**: 形成中の点だけが動いたときも追加は 0。
- **不変量 C（退化していない）**: 確定ぶんが入れ替わったら作り直す。
- **不変量 D（オーダー）**: 系列長 2 点（800 / 3000）で走査回数が変わらない。
- **不変量 E（値を古くしない）**: 形成中の点は毎回中央値に入る（持ち越しと引き換えに
  判定を止めない）。
"""
from __future__ import annotations

import numpy as np
import pytest

from dashboard_ui.adapter import series_role_table
from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.adapter.series_role_table import SeriesRoleTable
from dashboard_ui.usecase.sheet_models import SeriesRole, SheetInstance

_INSTANCE = SheetInstance("moving_averages", "default", {"length": 24}, "1m")


@pytest.fixture
def scans(monkeypatch):
    """確定ぶんの走査（O(n) の有限値抽出）だけを数える Test Spy。"""
    counted: "list[int]" = []
    original = series_role_table._finite_values          # noqa: SLF001

    def counting(values):
        counted.append(int(np.asarray(values).size))
        return original(values)

    monkeypatch.setattr(series_role_table, "_finite_values", counting)
    return counted


def _role(table: SeriesRoleTable, values, *, price: float = 100.0) -> SeriesRole:
    return table.role_of(instance=_INSTANCE, series_name="ma", values=tuple(values),
                         reference_price=price)


def _levels(length: int) -> "list[float]":
    return [100.0 + (index % 7) * 0.1 for index in range(length)]


def test_repeating_the_request_rescans_no_confirmed_values(scans) -> None:
    """不変量 A（2 点固定）: 繰り返し 5 / 20 のどちらでも追加は 0。"""
    additional = {}
    for repeats in (5, 20):
        table = SeriesRoleTable(store=MaterialStore())
        values = _levels(200)
        _role(table, values)
        warmed = len(scans)
        for _ in range(repeats):
            _role(table, values)
        additional[repeats] = len(scans) - warmed

    assert additional[5] == 0
    assert additional[20] == 0


def test_a_moving_forming_value_does_not_rescan_the_confirmed_values(scans) -> None:
    """不変量 B: 段 2 の鮮度（末尾 1 点が動く）は確定ぶんの走査を増やさない。"""
    table = SeriesRoleTable(store=MaterialStore())
    values = _levels(200)
    _role(table, values)
    warmed = len(scans)

    _role(table, [*values[:-1], 999.0])

    assert len(scans) - warmed == 0


def test_a_revised_confirmed_value_is_rescanned(scans) -> None:
    """不変量 C: 規則が「二度と走査しない」に退化していないこと（自己検査）。"""
    table = SeriesRoleTable(store=MaterialStore())
    values = _levels(200)
    _role(table, values)
    warmed = len(scans)

    revised = list(values)
    revised[40] = 123.4                     # 履歴の途中を遡って訂正する
    _role(table, revised)

    assert len(scans) - warmed > 0


def test_the_series_length_does_not_change_the_scan_count(scans) -> None:
    """不変量 D（2 点固定）: 系列長 800 / 3000 で走査回数は変わらない。"""
    counts = {}
    for length in (800, 3000):
        table = SeriesRoleTable(store=MaterialStore())
        scans.clear()
        for _ in range(4):
            _role(table, _levels(length))
        counts[length] = len(scans)

    assert counts[800] == counts[3000]


def test_the_forming_value_still_takes_part_in_the_median() -> None:
    """不変量 E: 持ち越すのは確定ぶんだけ。形成中の点は毎回中央値に入る。

    実値の帯は現在値の 0.3〜3 倍（境界を含む）。同じ確定ぶんでも形成中の点が動けば
    中央値が動き、判定が変わる。ここが止まると「持ち越したせいで水準が古くなる」。
    """
    table = SeriesRoleTable(store=MaterialStore())

    below = _role(table, (1.0, 3.0), price=10.0)      # 中央値 2.0 < 3.0（帯の下端）
    inside = _role(table, (1.0, 5.0), price=10.0)     # 中央値 3.0 = 帯の下端

    assert below is SeriesRole.NOT_LEVEL
    assert inside is SeriesRole.PRICE_LEVEL


def test_the_shared_median_equals_the_one_computed_from_scratch() -> None:
    """発行 − 使用 = 0 の裏返し: 持ち越しても判定は作り直したときと同じ。"""
    values = [*_levels(200), float("nan")]
    shared = SeriesRoleTable(store=MaterialStore())
    _role(shared, values)                              # 版を温める

    assert _role(shared, values) is _role(SeriesRoleTable(store=MaterialStore()), values)
