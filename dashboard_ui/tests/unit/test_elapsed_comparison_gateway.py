"""§5.3.3 / T-8 積み上がる量の比較集合（同じ経過の過去）を組み立てる面を固定する。

問題（§5.3.3 実測）: 形成中の足の部分和を**確定足の分布**へ当てると必ず極小に出る
（1h の経過 10% で `p` の中央値 0.000）。是正は「同じ経過まで進んだ過去の足」へ当てること。

T-8（丸め禁止）: 経過割合の丸めは不採用（実測 p90 |Δp| 0.10〜0.15 ＝ バイアスの再導入）。
素材の最小単位（tf >= 5m なら **1m 足**）で厳密に同経過を突き合わせる。

1m 自身にはサブ単位の供給が無い（ティック供給が要る）。ここでは比較集合を**作らない**：
確定足の分布へ当てて済ませるのは §5.3.3 のバイアスそのものだからである。作らなければ
usecase が `p=None` と理由を出す（無言の縮退にならない）。
"""
from __future__ import annotations

import pytest

from dashboard_ui.adapter.gateway.elapsed_comparison_gateway import (
    ElapsedComparisonGateway,
)
from dashboard_ui.usecase.sheet_models import OscillatorSpec, SheetInstance

REF = "jp225_tick"
#: 2026-08-28 20:00:00 UTC（5m 境界の上）。
START = 1_787_004_000
MINUTE = 60


class SeriesSpy:
    """P-1 の Test Spy（発行したキーを記録する）。"""

    def __init__(self, values) -> None:
        self.issued: "list[tuple[str, str, str]]" = []
        self._values = list(values)

    def full_series(self, *, indicator_id, variant, params, dataset_ref, timeframe):
        self.issued.append((indicator_id, variant, timeframe))
        return {
            "tickvol": tuple(
                (START + index * MINUTE, float(value))
                for index, value in enumerate(self._values)
            )
        }


def spec(cumulative: bool = True) -> OscillatorSpec:
    return OscillatorSpec(value_series="tickvol", band_high_series="tickvol_q90",
                          q_high=0.9, window_n=500, k_events=50, cumulative=cumulative)


def instance_of(timeframe: str) -> SheetInstance:
    return SheetInstance("tickvol", "default", {}, timeframe, intrabar_capable=True)


def comparisons_for(spy: SeriesSpy, *entries, minutes: int):
    """`minutes` 本目の 1m 足が形成中（＝直前までが完了）の状態で比較集合を組む。"""
    gateway = ElapsedComparisonGateway(series_port=spy)
    return gateway.comparisons(
        dataset_ref=REF,
        entries=[(instance, spec()) for instance in entries],
        now_unix=START + (minutes - 1) * MINUTE + 30,
    )


def test_the_pool_holds_only_the_completed_parent_bars() -> None:
    """因果境界: 形成中の親足は比較集合に入れない（自分自身と比べない）。"""
    spy = SeriesSpy([1, 2, 3, 4, 5,      # 20:00 の 5m 足（完了）
                     6, 7, 8, 9, 10,     # 20:05 の 5m 足（完了）
                     11, 12])            # 20:10 の 5m 足（形成中・12 は形成中の 1m）

    result = comparisons_for(spy, instance_of("5m"), minutes=12)
    comparison = result[instance_of("5m").key]

    assert comparison.pool.bar_count == 2
    assert list(comparison.pool.partial_sums_at(1)) == [1.0, 6.0]


def test_the_elapsed_units_count_only_the_completed_sub_units() -> None:
    """経過 `k` は**完了した 1m 本数**（形成中の 1m は数えない・T-8）。"""
    spy = SeriesSpy([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])

    comparison = comparisons_for(spy, instance_of("5m"), minutes=12)[
        instance_of("5m").key
    ]

    assert comparison.completed_units == 1        # 20:10 だけが完了（20:11 は形成中）
    assert comparison.forming_sum == 11.0


def test_the_partial_sum_is_compared_at_the_same_elapsed_point() -> None:
    """比較集合は「同じ経過まで進んだ過去の足」の部分和である。"""
    spy = SeriesSpy([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13])

    comparison = comparisons_for(spy, instance_of("5m"), minutes=13)[
        instance_of("5m").key
    ]

    assert comparison.completed_units == 2
    assert comparison.forming_sum == 11.0 + 12.0
    assert list(comparison.pool.partial_sums_at(2)) == [1.0 + 2.0, 6.0 + 7.0]


def test_the_chart_timeframe_of_the_sub_unit_has_no_comparison() -> None:
    """1m 自身はサブ単位の供給が無い（確定足の分布へ当てない＝バイアスを再導入しない）。"""
    spy = SeriesSpy([1, 2, 3])

    result = comparisons_for(spy, instance_of("1m"), minutes=3)

    assert result == {}
    assert spy.issued == []


def test_the_sub_unit_series_is_issued_once_for_every_timeframe() -> None:
    """T-1: 同じ 1m 系列を足の数だけ発行しない（束契約）。"""
    spy = SeriesSpy(list(range(1, 121)))

    result = comparisons_for(
        spy, instance_of("5m"), instance_of("15m"), instance_of("1h"), minutes=120
    )

    assert len(spy.issued) == 1
    assert len(result) == 3


def test_a_parent_bar_without_any_completed_sub_unit_has_no_comparison() -> None:
    """境界値: 親足が始まったばかり（完了 1m が 0 本）なら比較集合を作らない。"""
    spy = SeriesSpy([1, 2, 3, 4, 5, 6])       # 20:05 の 5m 足の 1 本目が形成中

    result = comparisons_for(spy, instance_of("5m"), minutes=6)

    assert result == {}


def test_no_completed_parent_bar_means_no_comparison() -> None:
    """境界値: 過去の親足が 1 本も完成していなければ当てる先が無い。"""
    spy = SeriesSpy([1, 2, 3])

    result = comparisons_for(spy, instance_of("5m"), minutes=3)

    assert result == {}


def test_a_non_cumulative_instance_is_not_supplied() -> None:
    """積み上がらない量に同経過の比較集合は要らない（余計な発行を作らない）。"""
    spy = SeriesSpy([1, 2, 3, 4, 5, 6])
    gateway = ElapsedComparisonGateway(series_port=spy)

    result = gateway.comparisons(
        dataset_ref=REF,
        entries=[(instance_of("5m"), spec(cumulative=False))],
        now_unix=START + 5 * MINUTE + 30,
    )

    assert result == {}
    assert spy.issued == []


def test_an_unknown_sub_unit_series_is_rejected() -> None:
    """宣言した系列が供給されないときは黙って空の比較集合を作らない。"""
    spy = SeriesSpy([1, 2, 3, 4, 5, 6])
    gateway = ElapsedComparisonGateway(series_port=spy)
    declared = OscillatorSpec(value_series="missing", band_high_series="tickvol_q90",
                              q_high=0.9, window_n=500, k_events=50, cumulative=True)

    with pytest.raises(ValueError, match="missing"):
        gateway.comparisons(
            dataset_ref=REF, entries=[(instance_of("5m"), declared)],
            now_unix=START + 6 * MINUTE + 30,
        )
