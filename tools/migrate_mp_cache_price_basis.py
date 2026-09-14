"""tools.migrate_mp_cache_price_basis — MP ディスクキャッシュを価格基準つきの鍵へ引き継ぐ（ISSUE-511 段階 1c）。

MP の 4 系統のキャッシュ（dwell / zp の mgrid・znull / tf-period）は、鍵に価格基準を含める
（``<root>/price-<basis>/<tree>/...``）。既存のキャッシュは価格基準の無い旧配置
（``<root>/<tree>/...``）にあるため、そのままでは読まれず再計算になる。本ツールは旧配置の
dir を新しい鍵の位置へ **複写するだけ** である（中身は 1 バイトも変えない＝再計算 0）。

なぜ名前変更ではなく複写か（実測 2026-09-14）:
    実キャッシュでの ``os.rename`` が ``EXDEV: Invalid cross-device link`` で失敗した（同一デバイス
    番号 64・置き場は overlayfs）。この置き場ではディレクトリの名前変更が成り立たない。複写なら
    置き場に依らず成り立つ。旧配置は **触らない**（消さない・動かさない）ので、戻す操作も要らない
    （旧コードへ戻しても旧配置をそのまま読める）。旧配置の掃除は GC の孤児判定で別に行う
    （削除は依頼者の承認事項）。

複写するのは台帳（marketdata/dataset_registry.py）にある木だけで、各木の価格基準も台帳から引く。
台帳に無い dir は触らない。移し先が既にある木は飛ばす（混ぜない・上書きしない）。

使い方:
    python -m tools.migrate_mp_cache_price_basis            # 計画だけ表示（既定・何も書かない）
    python -m tools.migrate_mp_cache_price_basis --apply    # 複写を実行
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence, Tuple

from market_profile_api.cache_layout_descriptor import price_basis_segment

#: 1 件の複写（旧配置 → 新配置）。
Copy = Tuple[Path, Path]


def plan_copies(roots: Iterable[Path], bases: Mapping[str, str]) -> "List[Copy]":
    """各 root の台帳の木について「``<root>/<tree>`` → ``<root>/price-<basis>/<tree>``」を計画する。

    何も書かない。旧配置が無い木（未計算）と、移し先が既にある木（移行済み・新しいコードが
    既に書き始めた）は計画に入れない（混ぜない・上書きしない）。
    """
    plan: "List[Copy]" = []
    for root in roots:
        root = Path(root)
        for tree, basis in sorted(bases.items()):
            src = root / tree
            dst = root / price_basis_segment(basis) / tree
            if src.is_dir() and not dst.exists():
                plan.append((src, dst))
    return plan


def _copy_tree(src: Path, dst: Path) -> None:
    """木 1 本を複写する（本ツールが発行する複写の単位・計算量検定の数える点）。

    ``shutil.copytree`` は下位 dir ごとに自身を再帰で呼ぶため、そこを数えると本ツールの発行数では
    なく下位 dir の数を数えてしまう。発行の単位は本関数とする。
    """
    shutil.copytree(src, dst)


def apply_copies(plan: Sequence[Copy]) -> None:
    """計画どおりに複写する（1 件 1 回・旧配置は触らない）。"""
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _copy_tree(src, dst)


def _ledger_bases() -> "dict[str, str]":
    from marketdata.dataset_registry import REGISTRY

    return {d.tick_token: d.price_basis for d in REGISTRY.values() if d.tick_token is not None}


def _cache_roots() -> "list[Path]":
    """4 系統の root（MP の公開面から引く。パス構成を本ツールに書き写さない）。"""
    from market_profile_api.cache_layout import current_layouts
    from market_profile_api.compute.store_port import zp_store

    roots = [Path(lay["root"]) for lay in current_layouts() if lay["root"] is not None]
    return roots + [zp_store().mgrid_root()]


def main(argv: "Sequence[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="複写を実行する")
    args = parser.parse_args(argv)

    plan = plan_copies(_cache_roots(), _ledger_bases())
    for src, dst in plan:
        print(f"{'copy' if args.apply else 'plan'}: {src} -> {dst}")
    if args.apply:
        apply_copies(plan)
    else:
        print(f"（dry-run: {len(plan)} 件。--apply で実行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
