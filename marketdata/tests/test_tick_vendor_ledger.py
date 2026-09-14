"""ティック ref の **ベンダ** を台帳へ載せる（ISSUE-515 対策 2）。

用語（初出定義）:
    ベンダ
        ＝ ティックをどこから受けているか。``"dukascopy"``（Dukascopy の配信）／``"mt5"``（OANDA MT5 端末の
          受信ジャーナル）。ライブ tick バッファは ref ごとに、このベンダの供給口から作る。

なぜ台帳か（実測 2026-09-13・ISSUE-515）:
    ライブ tick バッファはプロセスに 1 つだけで、Dukascopy から取った値を ref を問わず 5 経路へ配っていた。
    ``jp225_mt5`` を tick=True にすると MT5 のチャートへ Dukascopy のティックが混ざる。ベンダを
    素材の属性として明示する（ISSUE-508 の裁定）ことで、バッファを ref ごと・ベンダごとに作れる。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from marketdata import dataset_registry, tf_meta
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor


def test_jp225_tick_is_fed_by_dukascopy():
    """``jp225_tick`` のベンダは Dukascopy（既存のライブ供給のまま）。"""
    # Arrange / Act / Assert
    assert tf_meta.tick_vendor("jp225_tick") == "dukascopy"


def test_jp225_mt5_is_fed_by_mt5():
    """``jp225_mt5`` のベンダは MT5（ライブ tick バッファは MT5 自身の受信から作る）。"""
    # Arrange / Act / Assert
    assert tf_meta.tick_vendor("jp225_mt5") == "mt5"


def test_a_tick_descriptor_without_a_vendor_cannot_be_built(tmp_path):
    """``tick=True`` でベンダの無い記述子は構築時に ``ValueError``（どこからライブで受けるか決まらない）。"""
    # Arrange / Act / Assert
    with pytest.raises(ValueError) as exc:
        DatasetDescriptor(
            path=tmp_path / "x.csv", symbol="XXX", tick=True, tick_token="XXX", price_basis="mid"
        )
    assert "vendor" in str(exc.value)


@pytest.mark.parametrize("ref", ["sample", "jp225", "jp225_m1", "no_such_ref"])
def test_refs_without_ticks_have_no_vendor_through_the_window(ref):
    """ティック木を持たない ref（台帳外を含む）のベンダは ``None``。"""
    # Arrange / Act / Assert
    assert tf_meta.tick_vendor(ref) is None


def test_the_window_delegates_to_the_ledger(monkeypatch):
    """読取側の窓口は台帳の答えをそのまま返す（自前の写しを持たない）。"""
    # Arrange
    monkeypatch.setattr(dataset_registry, "tick_vendor", {"jp225_tick": "SENTINEL"}.get)

    # Act / Assert
    assert tf_meta.tick_vendor("jp225_tick") == "SENTINEL"


def test_every_tree_has_exactly_one_vendor():
    """同じ木を読む ref は同じベンダ（1 つの木を 2 つの供給口で満たさない）。"""
    # Arrange
    tokens = {d.tick_token for d in REGISTRY.values()} - {None}

    # Act
    vendors = {
        token: {d.vendor for d in REGISTRY.values() if d.tick_token == token} for token in tokens
    }
    split = {token: sorted(map(str, v)) for token, v in vendors.items() if len(v) != 1}

    # Assert
    assert vendors, "木を持つ ref が 1 つも無い（検定が空回りしている）"
    assert not split, f"同じ木に複数のベンダがある: {split}"
