"""ISSUE-177 item 2: 注入シームが Protocol を「宣言」でなく「強制」することの回帰ガード。

``store_port`` / ``tick_store_port`` の Port は ``@runtime_checkable`` な Protocol だが、
``set_zp_store`` / ``set_dwell_store`` / ``set_tick_store`` は ``isinstance`` 検査なしに
任意のオブジェクトを受理していた。Port を満たさない実装が注入されると、欠落メソッドが
実際に呼ばれるまで（＝serving 中の任意のタイミングまで）破綻が遅延する。

本モジュールは 3 setter を一括で固定する:
  - Port 非準拠の注入は **注入時点で** ``TypeError``（早期失敗）
  - Port 準拠の代替実装は受理される（構造的部分型＝既定具象派生である必要はない＝LSP）
  - ``None``（既定へ戻す）は常に受理される
  - composition root の既定具象は 3 つとも Port を満たす（本番結線を壊さない）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from market_profile_api.compute import store_port as sp
from market_profile_api.compute import tick_store_port as tsp
from test_store_gateway_layering import _FakeDwell, _FakeZp


class _NotAStore:
    """どの Port も満たさないオブジェクト（必須メソッドを 1 つも持たない）。"""


class _PartialZp:
    """``ZpStorePort`` の一部（CACHE_MISS ＋ null_path）しか持たない実装＝Port 非準拠。

    ISSUE-177 の実態: 拡充前の fake がこの形であり、宣言だけの Protocol では受理されていた。
    """

    CACHE_MISS = object()

    def null_path(self, symbol, day_start):  # noqa: ANN001
        return Path(f"/fake/zp/{symbol}/{day_start}.npz")


class _PartialDwell:
    """``DwellStorePort`` の一部（CACHE_MISS ＋ cache_path）しか持たない実装＝Port 非準拠。"""

    CACHE_MISS = object()

    def cache_path(self, symbol, day_start):  # noqa: ANN001
        return Path(f"/fake/dwell/{symbol}/{day_start}.npz")


class _PartialTick:
    """``TickStorePort`` の一部（data_dir のみ）しか持たない実装＝Port 非準拠（load_window_ticks 欠落）。"""

    def data_dir(self) -> Path:
        return Path("/fake/root")


class _FakeTick:
    """``TickStorePort``（DataRootPort ＋ TickReaderPort）を満たす代替実装。"""

    def data_dir(self) -> Path:
        return Path("/fake/root")

    def day_files(self, lo_day, hi_day, *, symbol):  # noqa: ANN001
        return []

    def load_window_ticks(self, symbol, start, end, *, columns, outlier_frac):  # noqa: ANN001
        from market_profile_api.compute.rollup_dto import TickWindow
        import numpy as np

        return TickWindow(secs=np.zeros(0, dtype=np.int64), mids=np.zeros(0))


@pytest.fixture(autouse=True)
def _restore_stores():
    """各テスト後に注入を既定（未注入）へ戻す（他テストへの状態漏れ防止）。"""
    yield
    sp.set_zp_store(None)
    sp.set_dwell_store(None)
    tsp.set_tick_store(None)


# --------------------------------------------------------------------------- #
# Port 非準拠の注入は注入時点で TypeError（早期失敗）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "setter, bad, port_name",
    [
        (sp.set_zp_store, _PartialZp(), "ZpStorePort"),
        (sp.set_zp_store, _NotAStore(), "ZpStorePort"),
        (sp.set_dwell_store, _PartialDwell(), "DwellStorePort"),
        (sp.set_dwell_store, _NotAStore(), "DwellStorePort"),
        (tsp.set_tick_store, _PartialTick(), "TickStorePort"),
        (tsp.set_tick_store, _NotAStore(), "TickStorePort"),
    ],
)
def test_non_conforming_store_injection_raises_type_error(setter, bad, port_name):
    """Port を満たさない実装の注入は ``TypeError``（欠落メソッド呼出まで破綻を遅延させない）。"""
    with pytest.raises(TypeError, match=port_name):
        setter(bad)


def test_rejected_injection_leaves_previous_store_intact():
    """拒否された注入は現在の Store を差し替えない（部分適用による中途半端な状態を作らない）。"""
    # Arrange: Port 準拠の代替実装を注入しておく。
    good = _FakeZp()
    sp.set_zp_store(good)

    # Act: Port 非準拠の注入を試みる。
    with pytest.raises(TypeError):
        sp.set_zp_store(_PartialZp())

    # Assert: 直前の注入実体が保持されている。
    assert sp.zp_store() is good


# --------------------------------------------------------------------------- #
# Port 準拠の代替実装・None は受理される
# --------------------------------------------------------------------------- #
def test_conforming_alternative_stores_are_accepted():
    """既定具象**非派生**でも Port を満たせば受理される（構造的部分型＝LSP を殺さない）。"""
    fz, fd, ft = _FakeZp(), _FakeDwell(), _FakeTick()
    sp.set_zp_store(fz)
    sp.set_dwell_store(fd)
    tsp.set_tick_store(ft)

    assert sp.zp_store() is fz
    assert sp.dwell_store() is fd
    assert tsp.tick_store() is ft


def test_none_resets_to_default_without_raising():
    """``None``（既定へ戻す）はガードを通過する。"""
    sp.set_zp_store(None)
    sp.set_dwell_store(None)
    tsp.set_tick_store(None)

    from market_profile_api.gateway.dwell_rollup_store import DwellRollupStore
    from market_profile_api.gateway.marketdata_tick_store import MarketdataTickStore
    from market_profile_api.gateway.zp_store import ZpStore

    assert isinstance(sp.zp_store(), ZpStore)
    assert isinstance(sp.dwell_store(), DwellRollupStore)
    assert isinstance(tsp.tick_store(), MarketdataTickStore)


# --------------------------------------------------------------------------- #
# 本番結線（composition root の既定具象）を壊さない
# --------------------------------------------------------------------------- #
def test_composition_root_defaults_pass_the_guard():
    """composition root が合成する既定具象は 3 つとも Port を満たし、setter を通過する。"""
    from market_profile_api.gateway.composition import (
        default_dwell_store,
        default_tick_store,
        default_zp_store,
    )

    dz, dd, dt = default_zp_store(), default_dwell_store(), default_tick_store()
    assert isinstance(dz, sp.ZpStorePort)
    assert isinstance(dd, sp.DwellStorePort)
    assert isinstance(dt, tsp.TickStorePort)
    # 実際に注入シームを通す（ガードが本番結線を拒否しないことの実挙動確認）。
    sp.set_zp_store(dz)
    sp.set_dwell_store(dd)
    tsp.set_tick_store(dt)
    assert sp.zp_store() is dz and sp.dwell_store() is dd and tsp.tick_store() is dt


def test_layout_is_not_part_of_the_port():
    """ISSUE-172 との両立: ``layout()`` は Port に含めない（含めると代替実装へ実装を強要する＝ISP 違反）。

    ``cache_layout.current_layouts()`` は既定具象の ``layout()`` を呼ぶが、これは Port の契約外である。
    """
    # Protocol の宣言メンバはクラス属性として存在する（load_null は在り・layout は無し）。
    assert hasattr(sp.ZpStorePort, "load_null") and not hasattr(sp.ZpStorePort, "layout")
    assert hasattr(sp.DwellStorePort, "load_day_rollup") and not hasattr(sp.DwellStorePort, "layout")
    # layout() を持たない Port 準拠実装も注入できる（ガードが layout を要求しない）。
    assert not hasattr(_FakeZp(), "layout")
    sp.set_zp_store(_FakeZp())
