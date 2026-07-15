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


def scan() -> "list[tuple[Path, str]]":
    """孤児候補 (path, 理由) を列挙する（現行世代はコード定数から導出）。

    実レイアウト（2026-07-15 実測）:
      dwell: <root>/<sym>/g<GRID_W>/*.npz（世代は npz メタ内 version＝ディレクトリ分離なし。
             旧 UTC 日キーの stale npz は同居するためファイル単位 GC は対象外＝注記のみ）
      zp:    <root>/{mgrid,znull}/<sym>/<世代>/...（znull 現行 b<ZP_BP>・旧 g10 等が孤児。mgrid は格子非依存＝温存）
      tfp:   <root>/<sym>/<tf>/<世代>/...（現行 s1=count・s3=zp。旧 g10/zp/s2 等が孤児）
    """
    import market_profile_api.compute.market_profile_dwell as mpd
    import market_profile_api.compute.market_profile_zp as zp
    from market_profile_api.controller import tf_period_profile_controller as tfp

    orphans: "list[tuple[Path, str]]" = []

    # dwell: <root>/<sym>/g* のうち現行 g{GRID_W:g} 以外は孤児。
    droot = Path(mpd._cache_root())
    if droot.is_dir():
        cur = f"g{mpd.GRID_W:g}"
        for sym in sorted(droot.iterdir()):
            if not sym.is_dir():
                continue
            for gen in sorted(sym.iterdir()):
                if gen.is_dir() and gen.name != cur:
                    orphans.append((gen, f"dwell 旧グリッド世代（現行 {cur}）"))

    # zp znull: <root>/znull/<sym>/<gen> のうち現行 b{ZP_BP:g} 以外は孤児（mgrid は格子非依存＝温存）。
    zroot = Path(zp._STORE.cache_root())
    zn = zroot / "znull"
    if zn.is_dir():
        cur = f"b{zp.ZP_BP:g}"
        for sym in sorted(zn.iterdir()):
            if not sym.is_dir():
                continue
            for gen in sorted(sym.iterdir()):
                if gen.is_dir() and gen.name != cur:
                    orphans.append((gen, f"zp znull 旧格子世代（現行 {cur}）"))

    # tf-period: <root>/<sym>/<tf>/<gen> のうち現行 {s1, s3} 以外は孤児（s2=旧VA・g10/zp=旧キー世代）。
    troot = tfp._tfp_disk_root()
    if troot and Path(troot).is_dir():
        for sym in sorted(Path(troot).iterdir()):
            if not sym.is_dir():
                continue
            for tf in sorted(sym.iterdir()):
                if not tf.is_dir():
                    continue
                for gen in sorted(tf.iterdir()):
                    if gen.is_dir() and gen.name not in ("s1", "s3"):
                        orphans.append((gen, "tf-period 旧世代（現行 s1=count / s3=zp）"))
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
