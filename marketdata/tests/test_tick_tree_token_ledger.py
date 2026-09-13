"""datasetRef → ティック木トークンの写像を **台帳へ載せる**（ISSUE-512 段階 1）。

木のレイアウトは日付の階層の下に「枝名 + _ticks.parquet」を置く形であり、その形の権威は
marketdata/tick_tree.py にある。しかし「どの datasetRef がどの枝を読むのか」はどこにも
書かれておらず、読取側（市場プロファイル・形成中バー）が各自の手書き写像や、木の側が持つ
既定引数（marketdata/tick_tree.py:30）で埋めていた。ref を足したときに
**何も言わずに他銘柄の木を読む**（出力は形式上正しいので状態検証では落ちない）。

本検定が固定するもの:
  1. 値ピン: ``jp225_tick`` の枝名は従来値のまま（表示不変の壁）。
  2. 台帳整合: ティック由来 ref の集合と、トークンを持つ ref の集合が一致する。
  3. Fail-Stop: ``tick=True`` なのにトークンが無い記述子は **例外**（既定値へ落ちない）。
  4. トークン規則: 台帳の全トークンがパス成分としてそのまま使える。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from marketdata import dataset_registry, path_tokens, tf_meta
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor


# --------------------------------------------------------------------------- #
# 1. 値ピン（表示不変の壁）
# --------------------------------------------------------------------------- #
def test_jp225_tick_reads_the_jp225_tree():
    """``jp225_tick`` の枝名は従来値のまま（既存の表示・読取先が動かない壁）。"""
    # Arrange / Act
    got = tf_meta.tick_tree_token("jp225_tick")

    # Assert
    assert got == "JP225", (
        f"jp225_tick の木トークンが {got!r} になっている。"
        " 既存の <DATA_DIR>/ticks/YYYY/MM/DD/JP225_ticks.parquet が読めなくなる。"
    )


def test_the_window_delegates_to_the_ledger(monkeypatch):
    """読取側の窓口は台帳へ**委譲**する（写しを持たない）。

    両者の戻り値を突き合わせる書き方は採らない。期待値と実測値の双方が被検査コードの
    呼び出しになり、窓口が台帳の実装を丸写ししていても通ってしまう（トートロジー）。
    台帳側を別物へ差し替え、窓口の答えがそれに追随することで委譲を示す。
    """
    # Arrange: 台帳側を、引数をそのまま印として返す別物へ差し替える。
    sentinel = {"jp225_tick": "SENTINEL-A", "no_such_ref": "SENTINEL-B"}
    monkeypatch.setattr(dataset_registry, "tick_tree_token", sentinel.get)

    # Act / Assert: 窓口は差し替えた答えを返す（＝自前の写しを持っていない）。
    assert tf_meta.tick_tree_token("jp225_tick") == "SENTINEL-A"
    assert tf_meta.tick_tree_token("no_such_ref") == "SENTINEL-B"


@pytest.mark.parametrize("ref", ["sample", "jp225", "jp225_m1", "jp225_mt5", "no_such_ref"])
def test_refs_without_a_tick_tree_have_no_token(ref):
    """ティック木を持たない ref（台帳外を含む）はトークンを持たない＝``None``。

    ``jp225_mt5`` が ``None`` であることは段階 1 の境界そのもの（tick フラグは段階 3）。
    """
    # Arrange / Act / Assert
    assert tf_meta.tick_tree_token(ref) is None


# --------------------------------------------------------------------------- #
# 2. 台帳整合
# --------------------------------------------------------------------------- #
def test_tick_refs_and_token_holders_are_the_same_set():
    """``TICK_REFS``（ティック由来 ref）＝ トークンを持つ ref。片方だけ増える余地を潰す。"""
    # Arrange / Act
    token_holders = {ref for ref in REGISTRY if tf_meta.tick_tree_token(ref) is not None}

    # Assert
    assert tf_meta.TICK_REFS == token_holders, (
        "ティック由来 ref とトークン保有 ref が食い違っている:"
        f" TICK_REFS={sorted(tf_meta.TICK_REFS)} / token={sorted(token_holders)}"
    )


# --------------------------------------------------------------------------- #
# 3. Fail-Stop（既定値へ落ちない）
# --------------------------------------------------------------------------- #
def test_a_tick_ref_without_a_token_fails_stop(monkeypatch, tmp_path):
    """``tick=True`` かつトークン未記入の記述子は ``ValueError``（既定値へ落ちない）。

    落ちること自体が要件である。既定の枝名へフォールバックすると、新しいティック ref が
    **無言で Dukascopy の木を読む**。出力は形式上正しい（OHLCV が揃う）ので、状態検証では
    原理的に検出できない。
    """
    # Arrange
    monkeypatch.setitem(
        REGISTRY,
        "tmp_tick_ref",
        DatasetDescriptor(
            path=tmp_path / "tmp.csv", symbol="XXX", tick=True, price_basis="mid", vendor="dukascopy"
        ),
    )

    # Act / Assert
    with pytest.raises(ValueError):
        tf_meta.tick_tree_token("tmp_tick_ref")


def test_fail_stop_message_names_the_ref(monkeypatch, tmp_path):
    """Fail-Stop のメッセージは、どの ref の記入漏れかを名指しする（直し方が分かる）。"""
    # Arrange
    monkeypatch.setitem(
        REGISTRY,
        "tmp_tick_ref",
        DatasetDescriptor(
            path=tmp_path / "tmp.csv", symbol="XXX", tick=True, price_basis="mid", vendor="dukascopy"
        ),
    )

    # Act
    with pytest.raises(ValueError) as exc:
        tf_meta.tick_tree_token("tmp_tick_ref")

    # Assert
    assert "tmp_tick_ref" in str(exc.value)


# --------------------------------------------------------------------------- #
# 4. トークン規則（パス成分としてそのまま使える）
# --------------------------------------------------------------------------- #
def test_every_token_is_already_a_safe_path_component():
    """台帳の全トークンは ``sanitize_path_component`` の不動点＝木の枝名に変換不要で載る。

    変換が要るトークンを台帳へ書くと、書いた名前と実際のディレクトリ名が食い違う。
    """
    # Arrange
    tokens = {
        ref: tf_meta.tick_tree_token(ref)
        for ref in REGISTRY
        if tf_meta.tick_tree_token(ref) is not None
    }

    # Act / Assert
    assert tokens, "トークンを持つ ref が 1 つも無い（台帳が空＝検定が空回りしている）"
    for ref, token in tokens.items():
        assert path_tokens.sanitize_path_component(token) == token, (
            f"{ref} のトークン {token!r} はパス成分としてそのまま使えない"
        )
