"""計算量 13（ISSUE-464 ①）: §5.3.3 の比較集合は epoch 単位で持ち越す。

比較集合（同じ経過まで進んだ過去の部分和）は最小単位（1m）の確定系列と、現在が 1m の
どの周期に居るかだけで決まる。したがって **1m の周期が進むまで不変**である。にもかかわらず
対象の時間足ごとに 1m 全点の周期始端を求め直していた（実測 2026-08-30・8 足束 1 要求:
42,023 回のスカラ呼び出し / 1,438 ms ＝ 3,000 点 × 2 回 × 7 足）。

出力は正しいままなので状態検証では原理的に落ちない（ISSUE-450 / ISSUE-257 と同型）。
CLAUDE.md 絶対命令 §4.1 に従い、測るのは時間ではなく**回数**である。回数そのものは
期待値に焼き込まない。固定するのは次の 5 つだけである。

- **不変量 A（要求で増えない）**: 1m の周期が進まない限り、要求を N 回繰り返しても
  **1m 全点の走査（完了単位の切り出し）と親足のまとめの追加は 0**。N は 2 点（5 / 20）。
  版を確かめるための周期始端 1 回は残る（O(1) の帳簿であり、素材の量には比例しない）。
  そのことを「追加の呼び出し回数が最小単位の点数 120 / 1200 で変わらない」で固定する。
- **不変量 B（対象足で増えない）**: 完了した 1m 単位の切り出し（1m 全点の走査）は
  対象の時間足が 2 → 4 に増えても **1 回**のまま（足ごとに切り直さない）。
- **不変量 C（鮮度と両立）**: 形成中の 1m 点だけが動いたときも追加は 0。
- **不変量 D（退化していない）**: 1m 素材が伸びたら作り直す。
- **不変量 E（発行 − 使用 = 0）**: 持ち越しても比較集合は作り直したときと等しい。
"""
from __future__ import annotations

import pytest

from dashboard_ui.adapter.gateway import elapsed_comparison_gateway as _gw
from dashboard_ui.adapter.gateway.elapsed_comparison_gateway import (
    ElapsedComparisonGateway,
)
from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.usecase.sheet_models import OscillatorSpec, SheetInstance

REF = "jp225_tick"
#: 2026-08-28 20:00:00 UTC（5m / 15m / 1h の境界の上）。
START = 1_787_004_000
MINUTE = 60


class SeriesSpy:
    """P-1 の Test Spy（1m の tickvol 系列を配るだけ）。"""

    def __init__(self, values) -> None:
        self._values = list(values)

    def replace(self, values) -> None:
        self._values = list(values)

    def full_series(self, *, indicator_id, variant, params, dataset_ref, timeframe):
        return {
            "tickvol": tuple(
                (START + index * MINUTE, float(value))
                for index, value in enumerate(self._values)
            )
        }


class PeriodSpy:
    """`period_start_unix` の Test Spy（周期始端の解決はこの面からしか起きない）。"""

    def __init__(self, monkeypatch) -> None:
        self.calls = 0
        original = _gw.period_start_unix

        def counted(unix, timeframe):
            self.calls += 1
            return original(unix, timeframe)

        monkeypatch.setattr(_gw, "period_start_unix", counted)


class ScanSpy:
    """最小単位の量に比例する仕事を数える Test Spy（ISSUE-464 ① の**無駄そのもの**）。

    `slices` … 完了単位の切り出し（1m 全点の走査・対象の足に依らない）。
    `folds`  … 親足でのまとめ（完了単位の走査・対象の足ごとに 1 つの出力を作る）。
    """

    def __init__(self, monkeypatch) -> None:
        self.slices = 0
        self.folds = 0
        original_slice = _gw._completed_units          # noqa: SLF001
        original_fold = _gw._comparison_of             # noqa: SLF001

        def counted_slice(points, now_unix, sub_timeframe):
            self.slices += 1
            return original_slice(points, now_unix, sub_timeframe)

        def counted_fold(completed, timeframe, now_unix):
            self.folds += 1
            return original_fold(completed, timeframe, now_unix)

        monkeypatch.setattr(_gw, "_completed_units", counted_slice)
        monkeypatch.setattr(_gw, "_comparison_of", counted_fold)


@pytest.fixture
def period_spy(monkeypatch) -> PeriodSpy:
    return PeriodSpy(monkeypatch)


@pytest.fixture
def scan_spy(monkeypatch) -> ScanSpy:
    return ScanSpy(monkeypatch)


def _spec() -> OscillatorSpec:
    return OscillatorSpec(value_series="tickvol", band_high_series="tickvol_q90",
                          q_high=0.9, window_n=500, k_events=50, cumulative=True)


def _instance(timeframe: str) -> SheetInstance:
    return SheetInstance("tickvol", "default", {}, timeframe, intrabar_capable=True)


def _minutes(count: int) -> "list[float]":
    return [float(index % 17 + 1) for index in range(count)]


def _request(spy: SeriesSpy, store: MaterialStore, *, timeframes, minutes: int):
    """要求 1 件ぶん（**口は要求ごとに組み直す**——共有するのはストアだけ）。"""
    gateway = ElapsedComparisonGateway(series_port=spy, store=store)
    return gateway.comparisons(
        dataset_ref=REF,
        entries=[(_instance(tf), _spec()) for tf in timeframes],
        now_unix=START + (minutes - 1) * MINUTE + 30,
    )


def test_repeating_the_request_rescans_no_sub_units(scan_spy: ScanSpy) -> None:
    """不変量 A（2 点固定）: 繰り返し 5 / 20 のどちらでも、量に比例する仕事の追加は 0。"""
    additional = {}
    for repeats in (5, 20):
        spy = SeriesSpy(_minutes(120))
        store = MaterialStore()
        _request(spy, store, timeframes=("5m", "15m"), minutes=120)
        warmed = (scan_spy.slices, scan_spy.folds)
        for _ in range(repeats):
            _request(spy, store, timeframes=("5m", "15m"), minutes=120)
        additional[repeats] = (scan_spy.slices - warmed[0], scan_spy.folds - warmed[1])

    assert additional[5] == (0, 0)
    assert additional[20] == (0, 0)


def test_the_remaining_bookkeeping_does_not_grow_with_the_material(period_spy) -> None:
    """不変量 A（帳簿の側・2 点固定）: 版を確かめる呼び出しは素材の量に比例しない。

    「追加が完全に 0」ではなく「追加が素材の量で増えない」を固定する。版が今のものかを
    確かめる 1 回は残るのが正しい（古い比較集合を配らないための代金であり、これを 0 に
    することは**版を見ないこと**と同義になる）。
    """
    additional = {}
    for length in (120, 1200):
        spy = SeriesSpy(_minutes(length))
        store = MaterialStore()
        _request(spy, store, timeframes=("5m", "15m"), minutes=length)
        warmed = period_spy.calls
        for _ in range(5):
            _request(spy, store, timeframes=("5m", "15m"), minutes=length)
        additional[length] = period_spy.calls - warmed

    assert additional[120] == additional[1200]


def test_more_target_timeframes_do_not_reslice_the_sub_units(scan_spy: ScanSpy) -> None:
    """不変量 B（2 点固定）: 対象足 2 → 4 でも 1m 全点の切り出しは 1 回。"""
    sliced = {}
    for timeframes in (("5m", "15m"), ("5m", "15m", "1h", "4h")):
        spy = SeriesSpy(_minutes(300))
        scan_spy.slices = 0

        _request(spy, MaterialStore(), timeframes=timeframes, minutes=300)
        sliced[len(timeframes)] = scan_spy.slices

    assert sliced[2] == sliced[4] == 1


def test_a_moving_forming_sub_unit_does_not_rescan(scan_spy: ScanSpy) -> None:
    """不変量 C: 形成中の 1m 点が動いても（周期は進まない）追加は 0。"""
    values = _minutes(120)
    spy = SeriesSpy(values)
    store = MaterialStore()
    _request(spy, store, timeframes=("5m", "15m"), minutes=120)
    warmed = (scan_spy.slices, scan_spy.folds)

    spy.replace([*values[:-1], 99.0])
    _request(spy, store, timeframes=("5m", "15m"), minutes=120)

    assert (scan_spy.slices, scan_spy.folds) == warmed


def test_a_new_sub_unit_rebuilds_the_comparison(period_spy) -> None:
    """不変量 D: 規則が「二度と作り直さない」に退化していないこと（自己検査）。"""
    values = _minutes(120)
    spy = SeriesSpy(values)
    store = MaterialStore()
    _request(spy, store, timeframes=("5m",), minutes=120)
    warmed = period_spy.calls

    spy.replace([*values, 7.0])                       # 1m が 1 本確定した
    _request(spy, store, timeframes=("5m",), minutes=121)

    assert period_spy.calls - warmed > 0


def test_the_shared_comparison_equals_the_one_built_from_scratch() -> None:
    """不変量 E: 持ち越しても比較集合は作り直したときと等しい。"""
    values = _minutes(120)
    spy = SeriesSpy(values)
    store = MaterialStore()
    _request(spy, store, timeframes=("5m", "15m"), minutes=120)

    shared = _request(spy, store, timeframes=("5m", "15m"), minutes=120)
    fresh = _request(spy, MaterialStore(), timeframes=("5m", "15m"), minutes=120)

    assert sorted(shared) == sorted(fresh)
    for key, comparison in shared.items():
        assert comparison.completed_units == fresh[key].completed_units
        assert comparison.forming_sum == fresh[key].forming_sum
        assert list(comparison.pool.partial_sums_at(1)) == list(
            fresh[key].pool.partial_sums_at(1)
        )
