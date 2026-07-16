"""MP キャッシュの世代 GC ツール（ISSUE-087 🟡-4）。既定 dry-run＝削除しない。

dwell/zp/tf-period のディスクキャッシュは世代付き subdir（バージョン・グリッド・パラメータを
パスへ埋め込み）で無効化される設計のため、世代を上げるたび旧 subdir が残置される。
本ツールは「現行コードが参照する世代」を実コード定数から導出し、それ以外を孤児候補として
サイズ付きで列挙する。削除は --delete 指定時のみ（運用では依頼者承認の上で実行する）。

実行: PYTHONPATH=.:indigators/market_profile/api:indigators/indicator_ui/api \
      python3 tools/cache_gc.py [--delete]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "indigators" / "market_profile" / "api"))
sys.path.insert(0, str(ROOT / "indigators" / "indicator_ui" / "api"))


def _du(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _fmt(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _walk_generation_dirs(root: Path, gen_depth: int) -> "list[Path]":
    """``root`` から ``gen_depth`` 階層下（世代 dir）を列挙する（各中間階層は dir のみ辿る）。

    gen_depth=1 は root 直下、2 は <sym>/<gen>、3 は <sym>/<tf>/<gen>。ソート順は各階層で安定。
    """
    level: "list[Path]" = [root]
    for _ in range(gen_depth):
        nxt: "list[Path]" = []
        for d in level:
            if d.is_dir():
                nxt.extend(sorted(c for c in d.iterdir() if c.is_dir()))
        level = nxt
    return level


def scan() -> "list[tuple[Path, str]]":
    """孤児候補 (path, 理由) を列挙する。

    現行世代の記述子は MP の公開契約 :func:`market_profile_api.cache_layout.current_layouts` のみを
    参照する（ISSUE-094 🔵: MP private 直結を排除）。GC は記述子（root / 世代階層 / 現行世代名）に
    従って世代 subdir を汎用走査し、現行世代以外を孤児候補として列挙する。
    """
    from market_profile_api.cache_layout import current_layouts

    orphans: "list[tuple[Path, str]]" = []
    for lay in current_layouts():
        root = lay["root"]
        if root is None or not Path(root).is_dir():
            continue
        current = lay["current"]
        reason = lay["reason"]
        for gen in _walk_generation_dirs(Path(root), int(lay["gen_depth"])):
            if gen.name not in current:
                orphans.append((gen, reason))
    return orphans


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delete", action="store_true",
                    help="孤児を削除する（既定は dry-run 列挙のみ・要依頼者承認）")
    args = ap.parse_args()
    orphans = scan()
    if not orphans:
        print("孤児世代なし。")
        return
    total = 0
    for path, reason in orphans:
        size = _du(path)
        total += size
        print(f"{_fmt(size):>8}  {path}  # {reason}")
    print(f"合計 {_fmt(total)}（{len(orphans)} エントリ）")
    if args.delete:
        for path, _ in orphans:
            shutil.rmtree(path)
        print("削除しました。")
    else:
        print("dry-run（削除は --delete。実行前に依頼者承認を得ること）")


if __name__ == "__main__":
    main()
