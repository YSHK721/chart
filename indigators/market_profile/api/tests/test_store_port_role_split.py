"""ISSUE-182 item3: 太いポートの役割別分割（ISP）の回帰ガード。

2 つの是正を固定する:

1. ``TickReaderPort.read_ticks`` の降格。実測（repo 全体 Grep）で外部クライアントは 0 件であり、
   唯一の呼出は既定具象 :class:`MarketdataTickStore` が自身の ``load_window_ticks`` の内部で
   行う自己呼出だった。Port に残すと「どのクライアントも要求しないメソッド」を代替実装へ
   強要する（ISP 違反）ため、gateway の private（``_read_ticks``）へ降格する。

2. ``ZpStorePort`` の役割別分割。7 メソッド ＋ 番兵の混載を ``cache_root`` 系 / mgrid 系 /
   znull 系へ分け、``tick_store_port`` が ISSUE-136 で自ら実施した規律（役割別 Port ＋
   狭い getter ＋ 単一注入シーム ＋ 後方互換の合成 Port）を揃える。合成 :class:`ZpStorePort` の
   **メンバ集合は分割前と厳密に一致**させ、``isinstance`` ガード（ISSUE-177）と既存注入面の
   意味論を変えない。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from market_profile_api.compute import store_port as sp
from market_profile_api.compute import tick_store_port as tsp

_PKG = Path(__file__).resolve().parents[1] / "market_profile_api"

#: 分割前（ISSUE-182 着手時点）の ``ZpStorePort`` メンバ集合の実測値。合成 Port はこれと一致し続ける
#: （＝``set_zp_store`` の ``isinstance`` ガードが受理/拒否する対象が分割で変わらないことの実証）。
_ZP_MEMBERS_BEFORE_SPLIT = frozenset({
    "CACHE_MISS",
    "cache_root",
    "mgrid_path",
    "null_path",
    "save_mgrid",
    "load_mgrid",
    "save_null",
    "load_null",
    "day_source_signature",
})


# --------------------------------------------------------------------------- #
# 1. read_ticks の降格（外部クライアント 0 件）
# --------------------------------------------------------------------------- #
def test_read_ticks_is_not_declared_by_the_tick_ports():
    """``read_ticks`` は Port の契約外（代替実装へ実装を強要しない）。"""
    assert "read_ticks" not in tsp.TickReaderPort.__protocol_attrs__
    assert "read_ticks" not in tsp.TickStorePort.__protocol_attrs__


def test_tick_port_members_are_exactly_the_ones_clients_use():
    """各 tick Port のメンバは実測クライアントが使うものだけで構成される（ISP）。"""
    assert set(tsp.DataRootPort.__protocol_attrs__) == {"data_dir"}
    assert set(tsp.TickReaderPort.__protocol_attrs__) == {"day_files", "load_window_ticks"}
    assert set(tsp.TickStorePort.__protocol_attrs__) == {
        "data_dir", "day_files", "load_window_ticks",
    }


def test_gateway_keeps_the_tick_file_read_private():
    """日別ファイル読取は既定具象の private へ降格する（公開面から外す）。"""
    from market_profile_api.gateway.marketdata_tick_store import MarketdataTickStore

    assert not hasattr(MarketdataTickStore, "read_ticks")
    assert callable(MarketdataTickStore._read_ticks)
    # 自己呼出（load_window_ticks 内）は private 名を通す。
    assert "self._read_ticks(" in inspect.getsource(MarketdataTickStore.load_window_ticks)


def test_store_without_read_ticks_passes_the_injection_guard():
    """``read_ticks`` を持たない代替実装も ``TickStorePort`` を満たし注入できる。"""

    class _NoReadTicks:
        def data_dir(self) -> Path:
            return Path("/fake/root")

        def day_files(self, lo_day, hi_day, *, symbol):  # noqa: ANN001
            return []

        def load_window_ticks(self, symbol, start, end, *, columns, outlier_frac):  # noqa: ANN001
            return None

    fake = _NoReadTicks()
    assert isinstance(fake, tsp.TickStorePort)
    tsp.set_tick_store(fake)
    try:
        assert tsp.tick_store() is fake
        assert tsp.tick_reader() is fake
    finally:
        tsp.set_tick_store(None)


# --------------------------------------------------------------------------- #
# 2. ZpStorePort の役割別分割（メンバ集合不変）
# --------------------------------------------------------------------------- #
def test_zp_composite_port_member_set_is_unchanged_by_the_split():
    """合成 Port のメンバ集合は分割前と厳密一致（注入面・isinstance ガードの意味論不変）。"""
    assert set(sp.ZpStorePort.__protocol_attrs__) == set(_ZP_MEMBERS_BEFORE_SPLIT)


def test_zp_role_ports_cover_the_composite_without_gap_or_surplus():
    """3 役割（基点 / mgrid / znull）の和が合成 Port と一致する（欠落も余剰もない）。"""
    root = set(sp.ZpCacheRootPort.__protocol_attrs__)
    mgrid = set(sp.ZpMgridStorePort.__protocol_attrs__)
    null = set(sp.ZpNullStorePort.__protocol_attrs__)

    assert root == {"cache_root"}
    assert mgrid == {"CACHE_MISS", "day_source_signature", "mgrid_path", "save_mgrid", "load_mgrid"}
    assert null == {"CACHE_MISS", "day_source_signature", "null_path", "save_null", "load_null"}
    assert root | mgrid | null == set(sp.ZpStorePort.__protocol_attrs__)
    # 実測: 番兵と署名は mgrid 経路（_mgrid_of_day）・znull 経路（_zp_day_rollup）の双方が呼ぶ
    #   ＝どちらか一方への帰属は不可能な共有契約（ZpDayInvalidationPort へ括る根拠）。
    assert mgrid & null == {"CACHE_MISS", "day_source_signature"}
    assert set(sp.ZpDayInvalidationPort.__protocol_attrs__) == {"CACHE_MISS", "day_source_signature"}


class _MgridOnly:
    """mgrid 役割だけを満たす実装（znull 系を持たない）。"""

    CACHE_MISS = object()

    def day_source_signature(self, symbol, day_start) -> str:  # noqa: ANN001
        return f"{symbol}:{int(day_start)}"

    def mgrid_path(self, symbol, day_start):  # noqa: ANN001
        return Path("/fake/zp/mgrid")

    def save_mgrid(self, path, grid, sig: str = "") -> None:  # noqa: ANN001
        return None

    def load_mgrid(self, path):  # noqa: ANN001
        return (self.CACHE_MISS, "")


class _NullOnly:
    """znull 役割だけを満たす実装（mgrid 系を持たない）。"""

    CACHE_MISS = object()

    def day_source_signature(self, symbol, day_start) -> str:  # noqa: ANN001
        return f"{symbol}:{int(day_start)}"

    def null_path(self, symbol, day_start):  # noqa: ANN001
        return Path("/fake/zp/znull")

    def save_null(self, path, roll, sig: str = "") -> None:  # noqa: ANN001
        return None

    def load_null(self, path):  # noqa: ANN001
        return (self.CACHE_MISS, "")


def test_zp_role_ports_are_independently_satisfiable():
    """役割 Port は互いに独立に満たせる（片方の実装を他方へ強要しない＝ISP）。"""
    m, n = _MgridOnly(), _NullOnly()

    assert isinstance(m, sp.ZpMgridStorePort) and not isinstance(m, sp.ZpNullStorePort)
    assert isinstance(n, sp.ZpNullStorePort) and not isinstance(n, sp.ZpMgridStorePort)
    # 合成 Port は全役割を要求する（片面実装は注入シームで拒否される＝ISSUE-177 のガード不変）。
    assert not isinstance(m, sp.ZpStorePort) and not isinstance(n, sp.ZpStorePort)


def test_partial_role_implementation_is_rejected_by_the_injection_seam():
    """片面実装の注入は従来どおり注入時点で ``TypeError``（ガードを緩めない）。"""
    with pytest.raises(TypeError, match="ZpStorePort"):
        sp.set_zp_store(_MgridOnly())
    assert sp._ZP_STORE is None  # 拒否は現在の Store を差し替えない。


def test_narrow_zp_getters_share_the_single_injection_seam():
    """``zp_mgrid_store()`` / ``zp_null_store()`` は単一の注入シームへ委譲する（挙動温存）。"""
    from test_store_gateway_layering import _FakeZp

    fz = _FakeZp()
    sp.set_zp_store(fz)
    try:
        assert sp.zp_store() is fz
        assert sp.zp_mgrid_store() is fz
        assert sp.zp_null_store() is fz
    finally:
        sp.set_zp_store(None)


def test_zp_clients_depend_on_their_role_getter_only():
    """実測クライアントは自分の役割 getter にのみ依存する（太い ``zp_store()`` 直参照なし）。"""
    from market_profile_api.compute import market_profile_zp as zp

    mgrid_src = inspect.getsource(zp._mgrid_of_day)
    assert "zp_mgrid_store()" in mgrid_src
    assert "zp_store()" not in mgrid_src

    null_src = inspect.getsource(zp._zp_day_rollup)
    assert "zp_null_store()" in null_src
    assert "zp_store()" not in null_src

    warmer = (_PKG / "compute" / "market_profile_zp_warmer.py").read_text(encoding="utf-8")
    assert "zp_null_store()" in warmer  # warmer は null_path のみ（znull 役割）。
    assert "zp_store()" not in warmer
