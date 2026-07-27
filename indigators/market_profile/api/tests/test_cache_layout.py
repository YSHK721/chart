"""cache_layout.current_layouts の公開契約テスト（ISSUE-094 🔵 item4 / ISSUE-172）。

GC ツール（tools/cache_gc.py）が依存する MP 公開記述子の形（キー・現行世代の定数連動）を固定する。

ISSUE-172: 記述子（gen_depth / current）を固定値で assert すると、実配置（各 Store の書込パス）が
変わったときに検出できない（dwell が `<sym>/v<ver>/g<grid>/` へ移行した後も `gen_depth=2` +
`current={"g10"}` のままとなり、**現行世代 `v4` が孤児判定**され旧 `g10` が温存された）。
本テストは記述子を「実際に書き込むパス」と突き合わせ、以下の不変条件を課す:

    root からの相対 parts のうち ``parts[gen_depth - 1]`` は必ず ``current`` に含まれる
    （＝GC が現に使用中のディレクトリを孤児として列挙しない）。
"""

from __future__ import annotations

from pathlib import Path

from market_profile_api import cache_layout
from market_profile_api.compute import market_profile_dwell as mpd
from market_profile_api.compute import market_profile_zp as zp
from market_profile_api.controller import tf_period_profile_controller as tfp
from market_profile_api.gateway import tf_period_disk_cache as tf_disk
# ISSUE-183 item5: 永続化設定（cache root / 形式版数）の単一情報源は gateway 側 cache_settings。
from market_profile_api.gateway import cache_settings as _mp_cache_settings

_DAY = 1339621200  # 任意の完了日 epoch（パス構成のみに使う・I/O しない）。


def _by_name() -> dict:
    return {lay["name"]: lay for lay in cache_layout.current_layouts()}


def _assert_generation_segment_is_current(lay: dict, real_path: Path) -> None:
    """記述子（gen_depth / current）が実書込パスの世代 segment と一致することを検証する。

    ``gen_depth`` は root から世代 dir までの階層数（世代 dir を含む）。実パスの相対 parts と
    突き合わせ、(1) 世代 dir がファイルより浅い階層にあること、(2) その名前が ``current`` に
    含まれること（＝GC が使用中 dir を孤児にしないこと）を課す。
    """
    root = lay["root"]
    assert root is not None, f"{lay['name']}: root 未解決"
    parts = Path(real_path).relative_to(Path(root)).parts
    depth = int(lay["gen_depth"])
    assert 1 <= depth <= len(parts) - 1, (
        f"{lay['name']}: gen_depth={depth} が実配置 {parts}（末尾はファイル）と不整合"
    )
    assert parts[depth - 1] in lay["current"], (
        f"{lay['name']}: 実配置の世代 dir '{parts[depth - 1]}'（{parts}）が "
        f"current={sorted(lay['current'])} に含まれない＝GC が現行世代を孤児判定する"
    )


def test_current_layouts_returns_three_systems_with_contract_keys():
    layouts = cache_layout.current_layouts()
    names = {lay["name"] for lay in layouts}
    assert names == {"dwell", "zp-znull", "tf-period"}
    for lay in layouts:
        assert set(lay.keys()) == {"name", "root", "gen_depth", "current", "reason"}
        assert lay["root"] is None or isinstance(lay["root"], Path)
        assert isinstance(lay["gen_depth"], int) and lay["gen_depth"] >= 1
        assert isinstance(lay["current"], frozenset) and lay["current"]
        assert isinstance(lay["reason"], str) and lay["reason"]


def test_current_generation_names_track_code_constants():
    by_name = _by_name()
    # dwell: 世代 dir は版数（ISSUE-089 で `<sym>/v<version>/g<grid>/` へ移行）。
    assert by_name["dwell"]["current"] == frozenset({f"v{_mp_cache_settings.DWELL_CACHE_VERSION}"})
    assert by_name["zp-znull"]["current"] == frozenset({f"b{zp.ZP_BP:g}"})
    # tf-period: count 世代は _TFP_CACHE_VERSION 連動・bucket は s1・zp 世代は s3。
    assert by_name["tf-period"]["current"] == frozenset(
        {f"s{tfp._TFP_CACHE_VERSION}", "s1", "s3"}
    )


def test_dwell_layout_matches_real_cache_path():
    """dwell: 記述子が :func:`market_profile_dwell._cache_path` の実配置と一致する。"""
    _assert_generation_segment_is_current(_by_name()["dwell"], mpd._cache_path("JP225", _DAY))


def test_zp_znull_layout_matches_real_null_path():
    """zp znull: 記述子が :meth:`ZpStore.null_path` の実配置と一致する。"""
    _assert_generation_segment_is_current(_by_name()["zp-znull"], zp.zp_store().null_path("JP225", _DAY))


def test_tf_period_layout_matches_every_real_write_path(monkeypatch, tmp_path):
    """tf-period: 記述子が controller の**全書込経路**（count/bucket/zp）の実配置と一致する。"""
    monkeypatch.setattr(tfp, "_TFP_CACHE_ROOT", str(tmp_path))
    lay = _by_name()["tf-period"]
    variants = tfp._disk_tf_variants("1h", float(mpd.GRID_W))
    assert len(variants) == 3
    for disk_tf in variants:
        _assert_generation_segment_is_current(
            lay, tf_disk.day_disk_path(Path(lay["root"]), "JP225", disk_tf, _DAY)
        )
