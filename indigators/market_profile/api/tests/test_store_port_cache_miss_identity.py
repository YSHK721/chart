"""ISSUE-177: CACHE_MISS 番兵の identity 判定が「既定具象派生」に暗黙依存しないことの回帰ガード。

事象（修正前）: ``compute/market_profile_zp.py`` / ``compute/market_profile_dwell.py`` は
module import 時に ``_CACHE_MISS = zp_cache_miss()`` を 1 回だけ評価し、既定具象
（``gateway.zp_store.ZpStore`` / ``gateway.dwell_rollup_store.DwellRollupStore``）のクラス属性
番兵を束縛していた。``StorePort`` は ``CACHE_MISS`` を Protocol の一部として宣言するため、
**Port 準拠だが既定具象非派生**の Store を :func:`set_zp_store` / :func:`set_dwell_store` で
注入すると、その Store が返す番兵は module 定数と identity 不一致になり、
``if disk is not _CACHE_MISS`` が **キャッシュミス番兵を実データとして受理**した
（LSP 破綻＝Port 準拠の代替実装が既定具象と置換できない）。

本モジュールは番兵取得を call-time（``zp_cache_miss()`` / ``dwell_cache_miss()``）へ移した
修正を固定する。フェイク Store はいずれも対応 Port の全メソッドを備える（``isinstance``
による Protocol 準拠を明示 assert する）＝「Port 準拠だが既定具象非派生」の最小再現である。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from market_profile_api.compute import market_profile_dwell as mpd
from market_profile_api.compute import market_profile_zp as zp
from market_profile_api.compute import store_port as sp

_DAY0 = 1704067200  # 2024-01-01 00:00 UTC（セッション日始端としてそのまま使う）
_NOW = _DAY0 + 10 * 86400  # 完了日として扱われる十分未来の as-of。


def _empty_ticks(symbol, start, end):
    """窓内ティックゼロ（→ mgrid/rollup とも None＝「実データ無しの完了日」）。"""
    return np.array([], dtype=np.int64), np.array([], dtype=np.float64)


class _PortOnlyZpStore:
    """``ZpStorePort`` を満たすが ``ZpStore`` 非派生の Store（独自 CACHE_MISS 番兵）。

    ``day_source_signature`` は空文字（＝ソースティックファイル不在時の既定具象と同じ挙動）を返し、
    ``load_mgrid`` は ``(CACHE_MISS, "")`` を返す＝署名は一致するが実体はミス、という
    実運用で起きる組み合わせを再現する。
    """

    CACHE_MISS = object()  # 既定具象 ZpStore.CACHE_MISS とは別実体。

    def __init__(self, root: Path, *, null_sig: str = "stale") -> None:
        self._root = Path(root)
        self._null_sig = null_sig
        self.saved_mgrid: list = []
        self.saved_null: list = []

    def cache_root(self) -> Path:
        return self._root

    def mgrid_path(self, symbol: str, day_start: int) -> Path:
        return self._root / "mgrid" / str(symbol) / f"{int(day_start)}.npz"

    def null_path(self, symbol: str, day_start: int) -> Path:
        return self._root / "znull" / str(symbol) / f"{int(day_start)}.npz"

    def save_mgrid(self, path: Path, grid, sig: str = "") -> None:
        self.saved_mgrid.append((path, grid, sig))

    def load_mgrid(self, path: Path):
        return self.CACHE_MISS, ""

    def save_null(self, path: Path, roll, sig: str = "") -> None:
        self.saved_null.append((path, roll, sig))

    def load_null(self, path: Path):
        # 署名不一致で「必ず再計算」へ落とす＝mgrid 側の番兵誤受理を単独で観測するため。
        return self.CACHE_MISS, self._null_sig

    def day_source_signature(self, symbol: str, day_start: int) -> str:
        return ""


class _PortOnlyDwellStore:
    """``DwellStorePort`` を満たすが ``DwellRollupStore`` 非派生の Store（独自 CACHE_MISS 番兵）。"""

    CACHE_MISS = object()  # 既定具象 DwellRollupStore.CACHE_MISS とは別実体。

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self.saved: list = []

    def cache_root(self) -> Path:
        return self._root

    def cache_path(self, symbol: str, day_start: int) -> Path:
        return self._root / str(symbol) / f"{int(day_start)}.npz"

    def save_day_rollup(self, path: Path, roll, sig: str = "") -> None:
        self.saved.append((path, roll, sig))

    def load_day_rollup(self, path: Path):
        return self.CACHE_MISS, ""

    def day_source_signature(self, symbol: str, day_start: int) -> str:
        return ""


@pytest.fixture
def zp_port_store(tmp_path, monkeypatch):
    """Port 準拠・非派生 Store を zp へ注入し、プロセス内キャッシュを隔離する。"""
    store = _PortOnlyZpStore(tmp_path / "zp")
    assert isinstance(store, sp.ZpStorePort)  # Port 準拠であることを実測で固定。
    monkeypatch.setattr(mpd, "_load_window_ticks", _empty_ticks)
    zp._reset_caches()
    sp.set_zp_store(store)
    try:
        yield store
    finally:
        sp.set_zp_store(None)
        zp._reset_caches()


@pytest.fixture
def dwell_port_store(tmp_path, monkeypatch):
    """Port 準拠・非派生 Store を dwell へ注入し、プロセス内キャッシュを隔離する。"""
    store = _PortOnlyDwellStore(tmp_path / "dwell")
    assert isinstance(store, sp.DwellStorePort)  # Port 準拠であることを実測で固定。
    monkeypatch.setattr(mpd, "_load_window_ticks", _empty_ticks)
    mpd._reset_caches()
    sp.set_dwell_store(store)
    try:
        yield store
    finally:
        sp.set_dwell_store(None)
        mpd._reset_caches()


def test_mgrid_of_day_honors_injected_store_cache_miss(zp_port_store):
    """注入 Store の CACHE_MISS を「ミス」として扱う（実データとして受理しない）。"""
    # Act
    got = zp._mgrid_of_day("SYN", _DAY0, _NOW)

    # Assert: 番兵はミス扱い → 再計算経路（ティックゼロ）→ None。
    assert got is not zp_port_store.CACHE_MISS
    assert got is None
    # プロセス内キャッシュが番兵で汚染されていない。
    assert zp_port_store.CACHE_MISS not in zp._MGRID_CACHE.values()


def test_zp_day_rollup_does_not_unpack_cache_miss_sentinel(zp_port_store):
    """番兵誤受理による ``closes, open_d = grid`` の TypeError が発生しない。"""
    # Act / Assert: 修正前は TypeError（object は unpack 不能）。
    got = zp._zp_day_rollup("SYN", _DAY0, _NOW)

    assert got is None
    assert got is not zp_port_store.CACHE_MISS


def test_day_rollup_honors_injected_store_cache_miss(dwell_port_store):
    """dwell 側も注入 Store の CACHE_MISS を「ミス」として扱う。"""
    # Act
    got = mpd._day_rollup("SYN", _DAY0, None, _NOW)

    # Assert
    assert got is not dwell_port_store.CACHE_MISS
    assert got is None
    assert dwell_port_store.CACHE_MISS not in mpd._DAY_CACHE.values()
