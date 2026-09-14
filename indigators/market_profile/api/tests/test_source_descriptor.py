"""ISSUE-097 🔴-1 回帰ガード: market_profile_controller の SourceDescriptor 登録表。

src（プロファイル計算ソース）の定義（許可集合・atom 表・metric 表・実処理 handler）を
単一レジストリ `_SOURCE_DESCRIPTORS` へ集約したことの不変条件を固定する。

- 導出された `_ALLOWED_SRC` / `_ATOM` / `_SRC_METRIC` が従来のハードコード値・順序と完全一致する
  （応答 byte／400 メッセージの `'|'.join(_ALLOWED_SRC)` を不変に保つ）。
- 各 src が正しい dispatch handler へ解決される（追加＝表への1エントリで済む構造の担保）。
"""

from __future__ import annotations

from market_profile_api.controller import market_profile_controller as mpc


# 従来（登録表導入前）のハードコード値。導出結果はこれとバイト一致でなければならない。
_HISTORICAL_ALLOWED_SRC = ("candle", "dwell", "m1", "zp")
_HISTORICAL_SRC_METRIC = {"dwell": "dwell", "m1": "count"}
_HISTORICAL_ATOM = {
    "candle": "足レンジ",
    "dwell": "tick滞在秒(セッション認識)",
    "m1": "tick数",
    "zp": "超過占有z(p)(分単位滞在/NullB)",
}


def test_allowed_src_derived_matches_historical_value_and_order():
    # tuple 比較は順序も検証する（400 メッセージの列挙順＝byte を固定）。
    assert mpc._ALLOWED_SRC == _HISTORICAL_ALLOWED_SRC


def test_src_metric_derived_matches_historical():
    assert mpc._SRC_METRIC == _HISTORICAL_SRC_METRIC


def test_atom_derived_matches_historical():
    assert mpc._ATOM == _HISTORICAL_ATOM


def test_registry_keys_equal_allowed_src():
    assert tuple(mpc._SOURCE_REGISTRY) == _HISTORICAL_ALLOWED_SRC


def test_registry_resolves_each_src_to_correct_handler():
    # candle は専用 handler、dwell/m1 は共通 _dispatch_dwell（metric で分岐）、zp は専用。
    assert mpc._SOURCE_REGISTRY["candle"].handler is mpc._dispatch_candle
    assert mpc._SOURCE_REGISTRY["dwell"].handler is mpc._dispatch_dwell
    assert mpc._SOURCE_REGISTRY["m1"].handler is mpc._dispatch_dwell
    assert mpc._SOURCE_REGISTRY["zp"].handler is mpc._dispatch_zp


def test_descriptor_metric_only_for_dwell_family():
    reg = mpc._SOURCE_REGISTRY
    assert reg["candle"].metric is None
    assert reg["dwell"].metric == "dwell"
    assert reg["m1"].metric == "count"
    assert reg["zp"].metric is None


def test_descriptor_atom_matches_historical_per_src():
    for src, atom in _HISTORICAL_ATOM.items():
        assert mpc._SOURCE_REGISTRY[src].atom == atom
