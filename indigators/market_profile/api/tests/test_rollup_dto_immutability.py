"""ISSUE-178: 層間 DTO（compute↔gateway 境界）の不変化の回帰ガード。

`indigators/PORTING_GUIDE.md` §2「DTO は不変」（``@dataclass(frozen=True)`` ＋ numpy 配列は
``__post_init__`` で ``writeable=False``）が market_profile の境界 DTO へ適用済みであることを固定する。

背景（修正前・実測）: 境界を跨ぐのは生 dict（``{kmin,dwell,cnt}`` / ``{kmin,obs,mean,var}``）と
タプル ``(secs, mids)`` で、``_DAY_CACHE`` / ``_NULL_CACHE`` はプロセス内キャッシュへ**参照**を
格納しそのまま呼出元へ返していた。in-place 更新が 1 箇所でも混入すればキャッシュが汚染される。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from market_profile_api.compute.rollup_dto import DayRollup, TickWindow, ZpRollup
from market_profile_api.gateway.marketdata_tick_store import MarketdataTickStore

_DTO_ARRAY_FIELDS = {
    DayRollup: ("dwell", "cnt"),
    ZpRollup: ("obs", "mean", "var"),
    TickWindow: ("secs", "mids"),
}


def _build(cls):
    if cls is DayRollup:
        return DayRollup(kmin=5, dwell=np.array([1.0, 2.0]), cnt=np.array([3.0, 4.0]))
    if cls is ZpRollup:
        return ZpRollup(
            kmin=-7, obs=np.array([1.0]), mean=np.array([2.0]), var=np.array([3.0])
        )
    return TickWindow(secs=np.array([10, 20], dtype=np.int64), mids=np.array([1.5, 2.5]))


@pytest.mark.parametrize("cls", list(_DTO_ARRAY_FIELDS))
def test_dto_is_frozen_dataclass(cls):
    """フィールド再代入を拒否する（``@dataclass(frozen=True)``）。"""
    dto = _build(cls)
    assert dataclasses.is_dataclass(dto)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(dto, _DTO_ARRAY_FIELDS[cls][0], np.zeros(1))


@pytest.mark.parametrize("cls", list(_DTO_ARRAY_FIELDS))
def test_dto_arrays_are_read_only(cls):
    """保持する numpy 配列は ``writeable=False``＝in-place 更新でキャッシュを汚染できない。"""
    dto = _build(cls)
    for name in _DTO_ARRAY_FIELDS[cls]:
        arr = getattr(dto, name)
        assert not arr.flags.writeable, f"{cls.__name__}.{name} が書込可能"
        with pytest.raises(ValueError, match="read-only"):
            arr[0] = 999.0


def test_tick_window_preserves_dtypes():
    """``secs`` は int64・``mids`` は float64（DTO 化で dtype を変えない＝下流の量子化に影響させない）。"""
    win = TickWindow(secs=[10, 20], mids=[1.5, 2.5])
    assert win.secs.dtype == np.int64
    assert win.mids.dtype == np.float64


def test_rollup_arrays_stay_usable_as_accumulation_source():
    """read-only 配列は累算の**右辺**として従来どおり使える（書込先は呼出元所有の可変配列）。"""
    roll = DayRollup(kmin=0, dwell=np.array([1.0, 2.0, 3.0]), cnt=np.zeros(3))
    dst = np.zeros(3)
    dst[0:3] += roll.dwell  # tf_period_columns / compute_dwell_profile と同型の累算。
    assert np.array_equal(dst, np.array([1.0, 2.0, 3.0]))


class _FakeTickStore(MarketdataTickStore):
    """day parquet I/O を差し替えた具象（窓復号のロジックだけを実行する）。"""

    _BASE = 1704067200

    def day_files(self, lo_day, hi_day, *, symbol):  # noqa: ANN001
        return [Path("/fake/ticks.parquet")]

    def _read_ticks(self, path, columns):  # noqa: ANN001  (ISSUE-182 item3: Port から private へ降格)
        secs = np.array([self._BASE + 1, self._BASE + 2, self._BASE + 3], dtype=np.int64)
        mid = np.array([20000.0, 20010.0, 20020.0])
        return pd.DataFrame(
            {"timestamp": pd.to_datetime(secs, unit="s"),
             "bidPrice": mid - 0.5, "askPrice": mid + 0.5}
        )


def test_gateway_load_window_ticks_returns_frozen_tick_window():
    """gateway→compute 境界（Port）は不変 DTO を返す（ISSUE-178 の実効点）。"""
    store = _FakeTickStore()
    win = store.load_window_ticks(
        "JP225", _FakeTickStore._BASE, _FakeTickStore._BASE + 86400,
        columns=["timestamp", "bidPrice", "askPrice"], outlier_frac=0.30,
    )
    assert isinstance(win, TickWindow)
    assert win.secs.dtype == np.int64 and win.mids.dtype == np.float64
    assert np.array_equal(win.mids, np.array([20000.0, 20010.0, 20020.0]))
    assert not win.secs.flags.writeable and not win.mids.flags.writeable


def test_empty_window_returns_frozen_tick_window():
    """ティック無し窓も同じ DTO 型（空配列・read-only）で返す。"""
    store = _FakeTickStore()
    win = store.load_window_ticks(
        "JP225", _FakeTickStore._BASE + 10 ** 6, _FakeTickStore._BASE + 10 ** 6 + 10,
        columns=["timestamp", "bidPrice", "askPrice"], outlier_frac=0.30,
    )
    assert isinstance(win, TickWindow)
    assert win.secs.size == 0 and win.mids.size == 0
    assert not win.secs.flags.writeable and not win.mids.flags.writeable


def test_dwell_shim_exposes_read_only_arrays():
    """compute 内部シム ``_load_window_ticks`` は 2 値タプルを保つが配列は read-only のまま。"""
    from market_profile_api.compute import market_profile_dwell as mpd
    from market_profile_api.compute import tick_store_port as tsp

    store = _FakeTickStore()
    prev = tsp._STORE
    tsp.set_tick_store(store)
    try:
        secs, mids = mpd._load_window_ticks(
            "JP225", _FakeTickStore._BASE, _FakeTickStore._BASE + 86400
        )
    finally:
        tsp.set_tick_store(prev)
    assert secs.dtype == np.int64 and mids.dtype == np.float64
    assert not secs.flags.writeable and not mids.flags.writeable
