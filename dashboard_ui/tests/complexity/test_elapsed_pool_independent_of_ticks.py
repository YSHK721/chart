"""計算量 6（§7 §5.3.3 固有）: 保持する分布の本数がティック数に比例しない。

§7 の表明そのもの: 1 バー内で何ティック来ても、更新は**経過割合の刻みごとに 1 回**。
T-8: 保持は 1m 系列の prefix cumsum 1 本・ティック数に非比例・窓の再評価は 1m 境界ごと。
"""
from __future__ import annotations

from dashboard_ui.domain.elapsed_fraction_pool import ElapsedFractionPool


def _pool(bar_count: int, units_per_bar: int) -> ElapsedFractionPool:
    keys, values = [], []
    for bar in range(bar_count):
        keys.extend([bar] * units_per_bar)
        values.extend([float(bar + 1)] * units_per_bar)
    return ElapsedFractionPool.from_units(keys, values)


def test_the_stored_amount_does_not_grow_with_ticks() -> None:
    """ティックは保持を増やさない（保持が増えるのはサブ単位が閉じたときだけ）。"""
    stored = {}
    for tick_count in (10, 1000):
        pool = _pool(20, 5)
        before = pool.unit_count

        for tick in range(tick_count):
            pool.partial_sums_at(3)          # ティックごとの参照

        stored[tick_count] = (before, pool.unit_count)

    assert stored[10] == stored[1000]


def test_the_comparison_window_is_not_rebuilt_within_a_unit() -> None:
    """窓の再評価は 1m 境界ごと。ティックごとに作り直さない。"""
    rebuilt = {}
    for tick_count in (10, 1000):
        pool = _pool(20, 5)
        seen = {id(pool.partial_sums_at(3))}

        for _ in range(tick_count):
            seen.add(id(pool.partial_sums_at(3)))

        rebuilt[tick_count] = len(seen)

    assert rebuilt[10] == rebuilt[1000] == 1


def test_closing_a_unit_rebuilds_the_window_exactly_once() -> None:
    """規則が「二度と作り直さない」に退化していないこと（自己検査）。"""
    pool = _pool(20, 5)
    first = pool.partial_sums_at(3)

    pool.close_unit(99, 7.0)
    second = pool.partial_sums_at(3)
    third = pool.partial_sums_at(3)

    assert second is not first
    assert third is second


def test_the_stored_amount_tracks_units_and_nothing_else() -> None:
    """オーダーの表明（2 点固定）: 保持はサブ単位数だけで決まる。"""
    assert _pool(20, 5).unit_count == 100
    assert _pool(40, 5).unit_count == 200


# ---------------------------------------------- 親足数に対する仕事量（🟡-4）
class ScanCountingKeys(list):
    """`in` の走査を **1 要素ずつ** 数える list（C 実装の list では数えられない）。

    重複検出が線形走査のままだと、サブ単位を 1 つ閉じるたびに親足数ぶん走査する
    ＝素材の取り込み全体が O(n²) になる。時間ではなく**走査した要素数**で表明する。
    """

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.scanned = 0

    def __contains__(self, value) -> bool:
        for item in self:
            self.scanned += 1
            if item == value:
                return True
        return False


def _fill(pool: ElapsedFractionPool, bar_count: int, units_per_bar: int) -> None:
    for bar in range(bar_count):
        for _ in range(units_per_bar):
            pool.close_unit(bar, float(bar + 1))


def test_closing_units_does_not_rescan_the_closed_bars() -> None:
    """オーダーの表明（2 点固定）: 取り込みの走査量が親足数に比例して増えない。"""
    scanned = {}
    for bar_count in (20, 40):
        pool = ElapsedFractionPool()
        pool._keys = ScanCountingKeys()          # noqa: SLF001 — 走査量を数える唯一の面
        _fill(pool, bar_count, 5)
        scanned[bar_count] = pool._keys.scanned  # noqa: SLF001

    assert scanned[20] == scanned[40]


def test_reading_the_comparison_set_derives_the_length_table_at_most_once(
    monkeypatch,
) -> None:
    """発行 − 使用 = 0: 長さの表（O(親足数)）は 1 回導けば足りる。

    足ごとに導き直すと、比較集合 1 本の取り出しが O(親足数²) になる。
    """
    original = ElapsedFractionPool.bar_lengths
    derived = {}

    for bar_count in (20, 40):
        calls = []

        def counting(self, _calls=calls):
            _calls.append(1)
            return original.fget(self)

        monkeypatch.setattr(ElapsedFractionPool, "bar_lengths", property(counting))
        pool = _pool(bar_count, 5)
        calls.clear()

        pool.partial_sums_at(3)
        derived[bar_count] = len(calls)

    assert derived[20] == derived[40]
    assert derived[20] <= 1
