"""ライブ tick バッファを **ref ごと（ベンダ・木ごと）** に作り、各経路が自分の ref のバッファだけを
引く（ISSUE-515 対策 2）。

なぜ必要か（実測 2026-09-13）:
    サーバはバッファを 1 つだけ作り、Dukascopy から取った値を ref を問わず /live_ticks・/forming_bar・
    /tf_period_profile・/market_profile_forming・末尾値計算の 5 経路へ配っていた。MT5 の ref を
    tick にすると、MT5 のチャートへ Dukascopy のティックが混ざる。出力は形式上正しいため（価格は
    ほぼ同じ水準）、状態検証では気付けない。

固定するもの:
  1. 台帳から作る: ref ごとに、そのベンダの供給口・木・価格基準でバッファを作る。
  2. 同じ木（同じベンダ）を読む ref は 1 つのバッファを共有する（同じ受信を 2 度しない）。
  3. 各経路は **自分の ref のバッファだけ** を引く。バッファを持たない ref は None（他の ref の
     バッファへ落とさない）。

data/: 実データを読まない（台帳への一時記述子と偽のバッファのみ）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from marketdata import tf_meta
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor

import framework.server as server_mod

_MT5_TOKEN = "JP225@OANDA-Japan-MT5-Live"


class _Recorder:
    """供給口（ベンダ → バッファ工場）の偽物。受けた (木, 基準) を控え、印つきのバッファを返す。"""

    def __init__(self, vendor: str) -> None:
        self.vendor = vendor
        self.built: "list[tuple[str, str]]" = []

    def __call__(self, token: str, basis: str):
        self.built.append((token, basis))
        return {"vendor": self.vendor, "token": token, "basis": basis}


@pytest.fixture
def two_vendors(monkeypatch, tmp_path):
    """jp225_tick（Dukascopy）に加え、MT5 の ref を 2 つ（同じ木）台帳へ一時的に載せる。"""
    for ref in ("zz_mt5_a", "zz_mt5_b"):
        monkeypatch.setitem(
            REGISTRY, ref,
            DatasetDescriptor(
                path=tmp_path / f"{ref}.csv", symbol="JP225", tick=True,
                tick_token=_MT5_TOKEN, price_basis="bid", vendor="mt5",
            ),
        )
    refs = frozenset(set(tf_meta.TICK_REFS) | {"zz_mt5_a", "zz_mt5_b"})
    monkeypatch.setattr(tf_meta, "TICK_REFS", refs)
    return refs


@pytest.fixture(autouse=True)
def _reset_buffers():
    server_mod.set_live_tick_buffers(None)
    yield
    server_mod.set_live_tick_buffers(None)


# --------------------------------------------------------------------------- #
# 1・2. 台帳から作る／同じ木は共有する
# --------------------------------------------------------------------------- #
def test_each_ref_gets_a_buffer_from_its_own_vendor(two_vendors):
    """ref ごとに、そのベンダの供給口・木・価格基準でバッファを作る。"""
    # Arrange
    feeds = {"dukascopy": _Recorder("dukascopy"), "mt5": _Recorder("mt5")}

    # Act
    buffers = server_mod.build_live_tick_buffers(feeds, refs=two_vendors)

    # Assert
    assert buffers["jp225_tick"] == {"vendor": "dukascopy", "token": "JP225", "basis": "mid"}
    assert buffers["zz_mt5_a"] == {"vendor": "mt5", "token": _MT5_TOKEN, "basis": "bid"}


def test_refs_reading_the_same_tree_share_one_buffer(two_vendors):
    """同じ木を読む 2 つの ref は 1 つのバッファを共有する（同じ受信を 2 度しない）。"""
    # Arrange
    feeds = {"dukascopy": _Recorder("dukascopy"), "mt5": _Recorder("mt5")}

    # Act
    buffers = server_mod.build_live_tick_buffers(feeds, refs=two_vendors)

    # Assert
    assert buffers["zz_mt5_a"] is buffers["zz_mt5_b"]
    assert feeds["mt5"].built == [(_MT5_TOKEN, "bid")], "同じ木のバッファを 2 つ作った"


def test_a_vendor_without_a_feed_is_refused(two_vendors):
    """供給口の無いベンダは作れない（黙って他のベンダのバッファを当てない）。"""
    # Arrange
    feeds = {"dukascopy": _Recorder("dukascopy")}

    # Act / Assert
    with pytest.raises(ValueError):
        server_mod.build_live_tick_buffers(feeds, refs=two_vendors)


# --------------------------------------------------------------------------- #
# 3. 各経路は自分の ref のバッファだけを引く
# --------------------------------------------------------------------------- #
class _Buffer:
    def __init__(self, ticks) -> None:
        self._ticks = ticks

    def ticks_since(self, ms):
        return list(self._ticks)


class _Untouchable:
    def ticks_since(self, ms):
        raise AssertionError("他の ref のバッファを引いた（ベンダが混ざる）")


def test_a_ref_reads_only_its_own_buffer(monkeypatch, two_vendors):
    """/tf_period_profile は、問い合わせた ref のバッファだけを controller へ渡す。"""
    # Arrange
    captured = {}

    def _spy(ref, timeframe, frm, to, src=None, live_ticks=None, va=None):
        captured["live_ticks"] = live_ticks
        return 200, {"ok": True}

    monkeypatch.setattr(server_mod, "handle_tf_period_profile", _spy)
    server_mod.set_live_tick_buffers(
        {"jp225_tick": _Untouchable(), "zz_mt5_a": _Buffer([[1000, 39000.0]])}
    )

    # Act
    server_mod._compute_tf_period_profile(
        {"datasetRef": ["zz_mt5_a"], "timeframe": ["5m"], "from": ["0"], "to": ["1"]}
    )

    # Assert
    assert captured["live_ticks"] == [[1000, 39000.0]]


def test_a_ref_without_a_buffer_gets_none_rather_than_another_vendors(monkeypatch, two_vendors):
    """バッファを持たない ref は None（他の ref のバッファへ落とさない）。"""
    # Arrange
    captured = {}

    def _spy(ref, timeframe, frm, to, src=None, live_ticks=None, va=None):
        captured["live_ticks"] = live_ticks
        return 200, {"ok": True}

    monkeypatch.setattr(server_mod, "handle_tf_period_profile", _spy)
    server_mod.set_live_tick_buffers({"jp225_tick": _Untouchable()})

    # Act
    server_mod._compute_tf_period_profile(
        {"datasetRef": ["zz_mt5_b"], "timeframe": ["5m"], "from": ["0"], "to": ["1"]}
    )

    # Assert
    assert captured["live_ticks"] is None
