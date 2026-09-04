"""cache_layout — MP ディスクキャッシュの「現行世代記述子」公開契約（ISSUE-094 🔵 item4）。

dwell / zp / tf-period のディスクキャッシュは世代付き subdir（バージョン・グリッド・パラメータを
パスへ埋め込む）で無効化される。世代を上げるたび旧 subdir が残置されるため、GC ツール
（:mod:`tools.cache_gc`）は「現行コードが参照する世代」を知る必要がある。従来 GC は MP の private
（``mpd._cache_root()`` / ``zp.zp_store()`` / ``tfp._tfp_disk_root()`` / グリッド定数）へ直結していた
（アクター横断の結合＝MP の内部変更が GC を壊す）。

本モジュールは **MP 側が所有する公開関数** :func:`current_layouts` を提供し、GC は本関数のみを参照する。
キャッシュ形式（root・世代 subdir の深さ・現行世代名）を知るのは MP の責務であり、GC は「記述子に
従って孤児 subdir を列挙する」汎用ロジックに縮退できる（SRP・境界衛生）。

ISSUE-172（配置記述子の単一情報源化）: 記述子の**生成**は本モジュールではなく、書込パスを組み立てる
当事者（:class:`~market_profile_api.gateway.dwell_rollup_store.DwellRollupStore` /
:class:`~market_profile_api.gateway.zp_store.ZpStore` /
:mod:`~market_profile_api.controller.tf_period_profile_controller`）が所有する。本モジュールは
:func:`current_layouts` で各所有者の記述子を**集約するだけ**であり、パス構成の知識を持たない。

    経緯: 従来は本モジュールが root / 世代深さ / 世代名を独自に書き下していたため、dwell が
    ``<sym>/g<grid>/`` から ``<sym>/v<version>/g<grid>/``（ISSUE-089）へ移行した際に記述子だけが
    旧形（``gen_depth=2`` + ``current={"g10"}``）に取り残された。結果、深さ 2 の実体である現行世代
    ``v4`` が ``{"g10"}`` と照合されて**孤児判定**され、旧 ``g10`` は温存される逆転が生じていた。

ISSUE-305（依存方向）: 記述子の**型**（:class:`CacheLayout` / :class:`CacheLayoutSource`）は
:mod:`market_profile_api.cache_layout_descriptor` が所有する。本モジュールは所有者を列挙する合成側
であり、型を同居させると 本モジュール → tf-period controller → 本モジュール の循環になるためである。
GC 向けの公開契約（:func:`current_layouts` の import 面）は従来どおり本モジュールが持つ。

記述子の形（:class:`CacheLayout`）:
    - ``name``     : 表示名（"dwell" / "zp-znull" / "tf-period"）。
    - ``root``     : 走査基点（``Path`` または未解決/無効時 ``None``）。
    - ``gen_depth``: root から世代 subdir までのディレクトリ階層数（世代 dir を含む）。
    - ``current``  : 現行世代 subdir 名の集合（これ以外の同階層 dir が孤児候補）。
    - ``reason``   : 孤児として列挙する際の理由文。

不変条件（所有者側で担保・``tests/test_cache_layout.py`` が実書込パスと突き合わせて検証する）:
    書込パスの root 相対 parts について ``parts[gen_depth - 1] in current`` が常に成り立つ
    （＝GC は現に書き込んでいるディレクトリを孤児として列挙しない）。
"""

from __future__ import annotations

from typing import Any

# ISSUE-305（依存方向）: 記述子の**型**は所有者（Store / controller）が依存する内側の境界のため
#   :mod:`market_profile_api.cache_layout_descriptor` へ分離した。本モジュールは所有者を列挙する
#   **合成**であり、型と同居させると 本モジュール → controller → 本モジュール の循環になる。
#   所有者は記述子モジュールを import する（本モジュールからは再エクスポートしない＝循環の再発を防ぐ）。
from market_profile_api.cache_layout_descriptor import CacheLayout, CacheLayoutSource

__all__ = ["current_layouts"]


def current_layouts() -> "list[dict[str, Any]]":
    """現行コードが参照する MP ディスクキャッシュ世代の記述子一覧を返す。

    各要素は :class:`CacheLayout` を dict 化したもの（``name`` / ``root`` / ``gen_depth`` /
    ``current`` / ``reason``）。``root`` は :class:`Path` または ``None``（未設定/無効）。

    ISSUE-172: 記述子の中身は本関数では組み立てず、書込パスの所有者
    （dwell Store / zp Store / tf-period controller）の ``layout()`` をそのまま集約する。
    世代名・世代深さは各所有者が自身のパス構成式から導出するため、パス変更・定数 bump の双方に
    構造的に追随する（本関数側のハードコードは 0）。
    """
    # 遅延 import（GC 実行時のみ・重い controller import 連鎖を module import 時に走らせない）。
    from market_profile_api.compute.store_port import dwell_store as _dwell_store
    from market_profile_api.compute.store_port import zp_store as _zp_store
    from market_profile_api.controller import tf_period_profile_controller as _tfp

    sources: "list[CacheLayoutSource]" = [_dwell_store(), _zp_store(), _tfp]
    layouts: "list[CacheLayout]" = [src.layout() for src in sources]

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
