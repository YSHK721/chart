"""ティック ref の **価格基準** を台帳へ載せる（ISSUE-515 対策 1）。

用語（初出定義）:
    価格基準（price basis）
        ＝ 生ティックの bid/ask のどちらを「価格」とするか。``"mid"``＝(bid+ask)/2、``"bid"``＝bid。
          値の語彙と規則の唯一源は :mod:`marketdata.tick_m1`（``PRICE_BASIS_MID`` / ``PRICE_BASIS_BID``）。

なぜ台帳か（実測 2026-09-13・ISSUE-515）:
    ``jp225_mt5`` の確定足は bid（``marketdata/mt5_ticks/ingest.py`` の ``PRICE_BASIS``）なのに、形成中
    バーと市場プロファイルは mid 固定だった。実データ 2026-09-11 の 360 分で形成中 − 確定の close は
    **中央値 +7.5・一致率 0**（確定のたびに約 7.5 跳ねる）。基準を「書き手だけが知る定数」にして
    おくと、読み手（形成中バー・MP・ライブバッファ）が同じ ref を別の基準で描く。

本検定が固定するもの:
  1. 値ピン: ``jp225_tick`` は mid のまま（表示不変の壁）。``jp225_mt5`` は書き手の基準と一致。
  2. 構築時の拒否: ``tick=True`` で基準の無い記述子は **作れない**（握り潰す網の届かない所で止める）。
  3. 窓口: 読取側は marketdata/tf_meta.py 経由で台帳へ委譲する（写しを持たない）。
  4. 木 → 基準: 同じ木を読む ref は同じ基準（MP は ref でなく木で読むため、木から一意に引ける）。
  5. 計算量: 基準の解決回数は穴の本数に比例しない（2 点で固定）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from marketdata import dataset_registry, tf_meta, tick_m1
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor


# --------------------------------------------------------------------------- #
# 1. 値ピン
# --------------------------------------------------------------------------- #
def test_jp225_tick_is_bid():
    """``jp225_tick`` の基準は bid（ISSUE-511 段階 1d・MT5 と同じ基準＝切り戻しで水準が跳ねない）。"""
    # Arrange / Act / Assert
    assert tf_meta.tick_price_basis("jp225_tick") == tick_m1.PRICE_BASIS_BID


def test_jp225_mt5_is_bid():
    """``jp225_mt5`` の基準は bid（MT5 端末が描いている系列・依頼者裁定 2026-09-02）。

    かつてここは ``marketdata.mt5_ticks.ingest`` の定数との一致を見ていた。段階 6（TBD-4）で
    その定数を廃し台帳が唯一の源になったので、ここが固定するのは**値そのもの**である
    （書き手が台帳から引いていることは ``marketdata/tests/test_mt5_price_basis.py`` が持つ）。
    ここが食い違うと、形成中バーは確定のたびに半スプレッド前後跳ねる（ISSUE-515 実測 +7.5）。
    """
    # Arrange / Act / Assert
    assert REGISTRY["jp225_mt5"].price_basis == tick_m1.PRICE_BASIS_BID


def test_every_declared_basis_is_a_known_basis():
    """台帳の基準は tick_m1 の語彙のどれか（綴り違いを台帳に書けない）。"""
    # Arrange
    known = {tick_m1.PRICE_BASIS_MID, tick_m1.PRICE_BASIS_BID}

    # Act
    declared = {d.price_basis for d in REGISTRY.values() if d.price_basis is not None}

    # Assert
    assert declared, "基準を持つ ref が 1 つも無い（検定が空回りしている）"
    assert declared <= known, f"未知の基準が台帳にある: {sorted(declared - known)}"


# --------------------------------------------------------------------------- #
# 2. 構築時の拒否
# --------------------------------------------------------------------------- #
def test_a_tick_descriptor_without_a_basis_cannot_be_built(tmp_path):
    """``tick=True`` で ``price_basis`` の無い記述子は構築時に ``ValueError``。

    読取時の Fail-Stop にしない理由: 形成中バーの読取は素材の失敗を握る包括的な except の内側で
    走る。そこで投げると WARNING と「注入しない素通し」へ化ける。構築時なら握る網が存在しない。
    """
    # Arrange / Act / Assert
    with pytest.raises(ValueError) as exc:
        DatasetDescriptor(path=tmp_path / "x.csv", symbol="XXX", tick=True, tick_token="XXX")
    assert "price_basis" in str(exc.value)


def test_a_non_tick_descriptor_needs_no_basis(tmp_path):
    """ティックを持たない ref は基準を要らない（従来の記述子はそのまま作れる）。"""
    # Arrange / Act
    d = DatasetDescriptor(path=tmp_path / "x.csv", symbol="XXX")

    # Assert
    assert d.price_basis is None


@pytest.mark.parametrize("ref", ["sample", "jp225", "jp225_m1", "no_such_ref"])
def test_refs_without_ticks_have_no_basis_through_the_window(ref):
    """ティック木を持たない ref（台帳外を含む）の基準は ``None``。"""
    # Arrange / Act / Assert
    assert tf_meta.tick_price_basis(ref) is None


# --------------------------------------------------------------------------- #
# 3. 窓口は台帳へ委譲する
# --------------------------------------------------------------------------- #
def test_the_window_delegates_to_the_ledger(monkeypatch):
    """読取側の窓口は台帳の答えをそのまま返す（自前の写しを持たない）。"""
    # Arrange
    monkeypatch.setattr(dataset_registry, "tick_price_basis", {"jp225_tick": "SENTINEL"}.get)

    # Act / Assert
    assert tf_meta.tick_price_basis("jp225_tick") == "SENTINEL"


# --------------------------------------------------------------------------- #
# 4. 木 → 基準
# --------------------------------------------------------------------------- #
def test_the_dukascopy_tree_is_read_as_bid():
    """Dukascopy の木（枝名 JP225）の基準は bid（ISSUE-511 段階 1d）。"""
    # Arrange / Act / Assert
    assert dataset_registry.price_basis_of_tick_token("JP225") == tick_m1.PRICE_BASIS_BID


def test_the_mt5_tree_is_read_as_bid():
    """MT5 の木の基準は bid（木から一意に引ける・段階 6 以降は台帳が唯一の源）。"""
    # Arrange
    token = REGISTRY["jp225_mt5"].tick_token

    # Act / Assert
    assert token is not None, "jp225_mt5 の木が台帳に無い"
    assert dataset_registry.price_basis_of_tick_token(token) == tick_m1.PRICE_BASIS_BID


def test_every_tree_has_exactly_one_basis():
    """同じ木を読む ref は同じ基準（木から一意に引ける）。割れていたら MP がどちらで描くか決まらない。"""
    # Arrange
    tokens = {d.tick_token for d in REGISTRY.values()} - {None}

    # Act
    bases = {
        token: {d.price_basis for d in REGISTRY.values() if d.tick_token == token}
        for token in tokens
    }
    split = {token: sorted(map(str, b)) for token, b in bases.items() if len(b) != 1}

    # Assert
    assert bases, "木を持つ ref が 1 つも無い（検定が空回りしている）"
    assert not split, f"同じ木に複数の基準がある: {split}"


def test_an_unknown_tree_is_refused():
    """台帳に無い木の基準は引けない（既定の mid へ落とさない）。"""
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        dataset_registry.price_basis_of_tick_token("NO_SUCH_TREE")
