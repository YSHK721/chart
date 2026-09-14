"""tools.migrate_mp_cache_price_basis — MP ディスクキャッシュを価格基準つきの鍵へ引き継ぐ（ISSUE-511 段階 1c）。

MP の 4 系統のキャッシュ（dwell / zp の mgrid・znull / tf-period）は、鍵に価格基準を含める
（``<root>/price-<basis>/<tree>/...``）。既存のキャッシュは価格基準の無い旧配置
（``<root>/<tree>/...``）にあるため、そのままでは読まれず再計算になる。本ツールは旧配置の
dir を新しい鍵の位置へ **名前変更するだけ** である（中身は 1 バイトも変えない＝再計算 0）。

動かすのは台帳（marketdata/dataset_registry.py）にある木だけで、各木の価格基準も台帳から引く。
台帳に無い dir は触らない。移し先が既にあれば何も動かさずに止める（混ぜない・上書きしない）。
戻すのも名前変更だけで可逆（``--revert``）。

使い方（稼働中のサーバを止めてから実行し、実行後に起動する。旧コードは旧配置へ書き続けるため）:
    python -m tools.migrate_mp_cache_price_basis            # 計画だけ表示（既定・何も動かさない）
    python -m tools.migrate_mp_cache_price_basis --apply    # 名前変更を実行
    python -m tools.migrate_mp_cache_price_basis --revert   # 新配置から旧配置へ戻す
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence, Tuple

from market_profile_api.cache_layout_descriptor import price_basis_segment

#: 1 件の移動（旧配置 → 新配置）。
Move = Tuple[Path, Path]


def plan_moves(roots: Iterable[Path], bases: Mapping[str, str]) -> "List[Move]":
    """各 root の台帳の木について「``<root>/<tree>`` → ``<root>/price-<basis>/<tree>``」を計画する。

    何も動かさない。旧配置が無い木（移行済み・未計算）は計画に入れない。

    Raises:
        FileExistsError: 移し先が既にあるとき（動かす前に止める）。
    """
    plan: "List[Move]" = []
    for root in roots:
        root = Path(root)
        for tree, basis in sorted(bases.items()):
            src = root / tree
            if not src.is_dir():
                continue
            dst = root / price_basis_segment(basis) / tree
            if dst.exists():
                raise FileExistsError(f"移し先が既にあります（動かしません）: {dst}")
            plan.append((src, dst))
    return plan


def apply_moves(plan: Sequence[Move]) -> None:
    """計画どおりに名前変更する（1 件 1 回・中身は変えない）。"""
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)


def revert_moves(plan: Sequence[Move]) -> None:
    """:func:`apply_moves` を逆順に戻す。移行で作った空の ``price-<basis>`` dir も片付ける。"""
    for src, dst in reversed(plan):
        os.rename(dst, src)
        try:
            dst.parent.rmdir()          # 空のときだけ消える（中身があれば残る）。
        except OSError:
            pass


def _ledger_bases() -> "dict[str, str]":
    from marketdata.dataset_registry import REGISTRY

    return {d.tick_token: d.price_basis for d in REGISTRY.values() if d.tick_token is not None}


def _cache_roots() -> "list[Path]":
    """4 系統の root（MP の公開面から引く。パス構成を本ツールに書き写さない）。"""
    from market_profile_api.cache_layout import current_layouts
    from market_profile_api.compute.store_port import zp_store

    roots = [Path(lay["root"]) for lay in current_layouts() if lay["root"] is not None]
    return roots + [zp_store().mgrid_root()]


def _revert_plan(roots: Iterable[Path], bases: Mapping[str, str]) -> "List[Move]":
    """新配置にある台帳の木を、旧配置へ戻す計画（戻し先が既にあれば止める）。"""
    plan: "List[Move]" = []
    for root in roots:
        root = Path(root)
        for tree, basis in sorted(bases.items()):
            dst = root / price_basis_segment(basis) / tree
            if not dst.is_dir():
                continue
            src = root / tree
            if src.exists():
                raise FileExistsError(f"戻し先が既にあります（動かしません）: {src}")
            plan.append((src, dst))
    return plan


def main(argv: "Sequence[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="名前変更を実行する")
    group.add_argument("--revert", action="store_true", help="新配置から旧配置へ戻す")
    args = parser.parse_args(argv)

    roots, bases = _cache_roots(), _ledger_bases()
    if args.revert:
        plan = _revert_plan(roots, bases)
        for src, dst in plan:
            print(f"revert: {dst} -> {src}")
        revert_moves(plan)
        return 0
    plan = plan_moves(roots, bases)
    for src, dst in plan:
        print(f"{'move' if args.apply else 'plan'}: {src} -> {dst}")
    if args.apply:
        apply_moves(plan)
    else:
        print(f"（dry-run: {len(plan)} 件。--apply で実行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
