"""JP225 ティックを year/month/day ディレクトリ構成で取得する自走ランナー。

各日 ``[day, day+1)``（UTC）を :class:`marketdata.DukascopyTickSource` で取得し
  ``<root>/<YYYY>/<MM>/<DD>/JP225_ticks.parquet``
へ保存する。**resume 対応**: 既存 parquet または空日マーカー（``JP225_ticks.empty``）が
ある日はスキップする。取得 0 件（休場/未提供）の日は空マーカーを置き再取得を防ぐ。
進捗は 1 日ごとに ``print(flush=True)`` するため ``nohup`` ログで追跡できる。

ベンダ隔離: dukascopy_python は marketdata adapter（DukascopyTickSource）に閉じる。

使い方（バックグラウンド自走・セッション非依存）:
  PYTHONPATH=/workspaces/app nohup python3 simulator/tools/fetch_ticks_ymd.py \
      --start 2025-01-01 --end 2026-06-26 --root data/marketdata/ticks \
      > /workspaces/app/data/marketdata/ticks/fetch.log 2>&1 &
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path


def _parse_date(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="JP225 ティックを y/m/d 構成で取得（resume 対応）")
    ap.add_argument("--start", type=_parse_date, required=True, help="開始日 YYYY-MM-DD（含む・UTC）")
    ap.add_argument("--end", type=_parse_date, required=True, help="終了日 YYYY-MM-DD（含まない・UTC）")
    ap.add_argument("--root", type=Path, default=Path("data/marketdata/ticks"),
                    help="出力ルート（既定 data/marketdata/ticks）")
    return ap


def _day_paths(root: Path, day: dt.datetime) -> "tuple[Path, Path, Path]":
    """``day`` の (ディレクトリ, parquet, .empty マーカー) を返す。

    レイアウトの単一権威は :func:`marketdata.tick_m1.day_parquet_path`（ISSUE-262）。
    かつてここは ``root/YYYY/MM/DD/JP225_ticks.parquet`` を自前で組んでおり、権威側の宣言
    「レイアウト変更を本所 1 箇所に閉じる」が事実と食い違っていた。
    """
    from marketdata.tick_m1 import day_parquet_name  # 遅延: import 副作用を実行時に限定

    name = day_parquet_name()
    d = root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
    return d, d / name, d / (name[: -len(".parquet")] + ".empty")


def run(start: dt.datetime, end: dt.datetime, root: Path) -> int:
    """[start, end) を 1 日ずつ取得し y/m/d へ保存。累計取得ティック数を返す。"""
    from marketdata import DukascopyTickSource, JP225  # 遅延: ここで dukascopy_python を要求

    src = DukascopyTickSource(instrument=JP225)
    total = 0
    day = start
    while day < end:
        nxt = day + dt.timedelta(days=1)
        d, out, marker = _day_paths(root, day)
        if out.exists() or marker.exists():
            print(f"skip  {day:%Y-%m-%d} (取得済み)", flush=True)
            day = nxt
            continue
        try:
            df = src.fetch_ticks(day, nxt)
        except Exception as exc:  # noqa: BLE001 — 1 日失敗で全体を止めない（次日へ継続）
            print(f"ERR   {day:%Y-%m-%d}: {type(exc).__name__}: {exc}", flush=True)
            day = nxt
            continue
        d.mkdir(parents=True, exist_ok=True)
        if df is None or len(df) == 0:
            marker.write_text("")  # 空日マーカー（休場/未提供）→ 再取得しない
            print(f"empty {day:%Y-%m-%d} (0 ticks)", flush=True)
        else:
            df.to_parquet(out, index=False)
            total += len(df)
            print(f"ok    {day:%Y-%m-%d}: {len(df)} ticks -> {out} (累計 {total})", flush=True)
        day = nxt
    print(f"DONE total={total} ({start:%Y-%m-%d}..{end:%Y-%m-%d})", flush=True)
    return total


def main() -> None:
    a = build_arg_parser().parse_args()
    run(a.start, a.end, a.root)


if __name__ == "__main__":
    main()
