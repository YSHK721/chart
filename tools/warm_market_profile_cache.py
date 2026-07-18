"""Market Profile 日別成果物ディスクキャッシュのウォーマー CLI（ISSUE-133 SRP）。

dwell（滞在秒ロールアップ）と zp（mgrid＋znull）の完了日成果物をディスクへ一括構築する運用バッチ CLI。
統計コア／キャッシュ協調モジュールからウォーマー CLI アクターを分離し tools/ 配下へ移設した。
集計・キャッシュ協調の実体は ``market_profile_api.compute.market_profile_{dwell,zp}_warmer``（薄い
call-time 委譲）が担い、本スクリプトは argparse による起動殻のみを持つ。

実行:
  PYTHONPATH=.:indigators/market_profile/api:indigators/indicator_ui/api \
      python3 tools/warm_market_profile_cache.py --src dwell --warm jp225_tick [--start ..] [--end ..]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "indigators" / "market_profile" / "api"))
sys.path.insert(0, str(ROOT / "indigators" / "indicator_ui" / "api"))


def main(argv: "list[str] | None" = None) -> None:
    parser = argparse.ArgumentParser(
        description="Market Profile 日別成果物（dwell / zp）のディスクキャッシュ・ウォーマー"
    )
    parser.add_argument(
        "--src", choices=("dwell", "zp"), default="dwell",
        help="ウォーム対象（dwell=滞在秒ロールアップ / zp=mgrid＋znull・既定 dwell）",
    )
    parser.add_argument(
        "--warm", metavar="REF_OR_SYMBOL", required=True,
        help="datasetRef（例 jp225_tick）または実ティック symbol（例 JP225）",
    )
    parser.add_argument("--start", default=None, help="期間開始（例 2020-01-01・既定 全期間）")
    parser.add_argument("--end", default=None, help="期間終了（例 2024-12-31・既定 当日）")
    args = parser.parse_args(argv)

    from market_profile_api.compute import market_profile_dwell as mpd

    sym = mpd.resolve_symbol(args.warm) or args.warm  # ref なら symbol へ解決、それ以外は symbol とみなす。

    if args.src == "zp":
        from market_profile_api.compute.market_profile_zp import _STORE as _ZP_STORE
        from market_profile_api.compute.market_profile_zp_warmer import warm_zp_cache

        print(f"[warm-zp] cache root = {_ZP_STORE.cache_root()}")
        warm_zp_cache(sym, start=args.start, end=args.end)
    else:
        from market_profile_api.compute.market_profile_dwell_warmer import warm_dwell_cache

        print(f"[warm] cache root = {mpd._cache_root()}")
        warm_dwell_cache(sym, start=args.start, end=args.end)


if __name__ == "__main__":
    main()
