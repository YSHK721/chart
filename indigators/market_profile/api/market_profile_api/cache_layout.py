"""cache_layout — MP ディスクキャッシュの「現行世代記述子」公開契約（ISSUE-094 🔵 item4）。

dwell / zp / tf-period のディスクキャッシュは世代付き subdir（バージョン・グリッド・パラメータを
パスへ埋め込む）で無効化される。世代を上げるたび旧 subdir が残置されるため、GC ツール
（:mod:`tools.cache_gc`）は「現行コードが参照する世代」を知る必要がある。従来 GC は MP の private
（``mpd._cache_root()`` / ``zp.zp_store()`` / ``tfp._tfp_disk_root()`` / グリッド定数）へ直結していた
（アクター横断の結合＝MP の内部変更が GC を壊す）。

本モジュールは **MP 側が所有する公開関数** :func:`current_layouts` を提供し、GC は本関数のみを参照する。
キャッシュ形式（root・世代 subdir の深さ・現行世代名）を知るのは MP の責務であり、GC は「記述子に
従って孤児 subdir を列挙する」汎用ロジックに縮退できる（SRP・境界衛生）。

記述子の形（:class:`CacheLayout`）:
    - ``name``     : 表示名（"dwell" / "zp-znull" / "tf-period"）。
    - ``root``     : 走査基点（``Path`` または未解決/無効時 ``None``）。
    - ``gen_depth``: root から世代 subdir までのディレクトリ階層数（世代 dir を含む）。
    - ``current``  : 現行世代 subdir 名の集合（これ以外の同階層 dir が孤児候補）。
    - ``reason``   : 孤児として列挙する際の理由文。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CacheLayout:
    """1 キャッシュ系統の世代レイアウト記述子（GC はこれのみを参照する）。"""

    name: str
    root: "Path | None"
    gen_depth: int
    current: "frozenset[str]"
    reason: str


def current_layouts() -> "list[dict[str, Any]]":
    """現行コードが参照する MP ディスクキャッシュ世代の記述子一覧を返す。

    各要素は :class:`CacheLayout` を dict 化したもの（``name`` / ``root`` / ``gen_depth`` /
    ``current`` / ``reason``）。``root`` は :class:`Path` または ``None``（未設定/無効）。世代名は
    実コード定数（``GRID_W`` / ``ZP_BP`` / ``_TFP_CACHE_VERSION``）から導出するため、定数 bump に
    追随する（GC 側のハードコードを排除）。
    """
    # 遅延 import（GC 実行時のみ・重い controller import 連鎖を module import 時に走らせない）。
    from market_profile_api.compute import market_profile_dwell as _mpd
    from market_profile_api.compute import market_profile_zp as _zp
    from market_profile_api.controller import tf_period_profile_controller as _tfp

    layouts: "list[CacheLayout]" = []

    # dwell: <root>/<sym>/g<GRID_W> のうち現行 g{GRID_W:g} 以外の g* が孤児（npz メタ版はファイル内）。
    dwell_root = Path(_mpd._cache_root())
    layouts.append(CacheLayout(
        name="dwell",
        root=dwell_root,
        gen_depth=2,  # <sym>/<gen>
        current=frozenset({f"g{_mpd.GRID_W:g}"}),
        reason=f"dwell 旧グリッド世代（現行 g{_mpd.GRID_W:g}）",
    ))

    # zp znull: <root>/znull/<sym>/b<ZP_BP> のうち現行 b{ZP_BP:g} 以外が孤児（mgrid は格子非依存＝温存）。
    zn_root = Path(_zp.zp_store().cache_root()) / "znull"  # ISSUE-137: StorePort 経由（旧 _zp._STORE）。
    layouts.append(CacheLayout(
        name="zp-znull",
        root=zn_root,
        gen_depth=2,  # <sym>/<gen>
        current=frozenset({f"b{_zp.ZP_BP:g}"}),
        reason=f"zp znull 旧格子世代（現行 b{_zp.ZP_BP:g}）",
    ))

    # tf-period: <root>/<sym>/<tf>/s<gen> のうち現行 {count=s{_TFP_CACHE_VERSION}, zp=s3} 以外が孤児。
    tfp_root = _tfp._tfp_disk_root()
    tfp_current = frozenset({f"s{_tfp._TFP_CACHE_VERSION}", "s3"})
    layouts.append(CacheLayout(
        name="tf-period",
        root=Path(tfp_root) if tfp_root else None,
        gen_depth=3,  # <sym>/<tf>/<gen>
        current=tfp_current,
        reason=f"tf-period 旧世代（現行 count=s{_tfp._TFP_CACHE_VERSION} / zp=s3）",
    ))

    return [
        {
            "name": lay.name,
            "root": lay.root,
            "gen_depth": lay.gen_depth,
            "current": lay.current,
            "reason": lay.reason,
        }
        for lay in layouts
    ]
