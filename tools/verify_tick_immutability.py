#!/usr/bin/env python3
"""過去日ティックの不変性を実測する（ロールアップ方式の前提検証・読取のみ）。

保存済み parquet（過去 UTC 日）と、いま再取得した同一日を突合し、
「訂正（同一 timestamp の値変化）」と「追記/欠落（timestamp 集合の差）」を分離して判定する。

- 訂正 or 欠落があれば「過去データは可変」＝『確定分をロールアップへ畳んで再読しない』前提は偽。
- 差が追記のみ / 皆無なら「不変」＝前提を支持。

安全性: ネットワーク再取得はメモリ上のみ。保存 parquet は一切上書きしない（読取専用）。

実行:
    python tools/verify_tick_immutability.py            # 既定 D-1/D-3/D-7/D-30
    python tools/verify_tick_immutability.py 1 5 20     # 齢（日）を明示
"""
from __future__ import annotations

import datetime as dt
import sys
from collections import Counter
from pathlib import Path

# repo 根を sys.path へ（marketdata を import するため）。
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd  # noqa: E402

from marketdata import JP225, DukascopyTickSource  # noqa: E402
from marketdata.paths import DATA_DIR  # noqa: E402
from marketdata.tick_m1 import day_parquet_path, tick_root  # noqa: E402

TOL = 1e-6  # float 比較許容（表現差を訂正と誤認しない）
# 生ティックの正準列の唯一源（ISSUE-262）。
from simulator.tools.ingest_ticks import RAW_COLUMNS  # noqa: E402

COLS = list(RAW_COLUMNS)
VALUE_COLS = tuple(RAW_COLUMNS[1:])  # timestamp を除く数値列


def _saved_days() -> list[dt.date]:
    days = []
    for p in tick_root(DATA_DIR).rglob("*_ticks.parquet"):
        parts = p.parts
        try:
            days.append(dt.date(int(parts[-4]), int(parts[-3]), int(parts[-2])))
        except Exception:
            pass
    return sorted(set(days))


def _pick(days: list[dt.date], today: dt.date, offset: int) -> dt.date | None:
    """today-offset 以下で最も新しい保存日（取引日）を返す。"""
    cand = [d for d in days if d <= today - dt.timedelta(days=offset)]
    return cand[-1] if cand else None


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df[[c for c in COLS if c in df.columns]].copy()
    ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None)
    df["ts_ns"] = ts.astype("int64")
    for c in VALUE_COLS:
        if c in df.columns:
            df[c] = (df[c].astype(float) / TOL).round().astype("int64")  # 許容誤差で量子化
    return df.sort_values("ts_ns", kind="stable").reset_index(drop=True)


def _tuples(df: pd.DataFrame) -> list[tuple]:
    valcols = [c for c in VALUE_COLS if c in df.columns]
    return [tuple(r) for r in df[["ts_ns"] + valcols].itertuples(index=False, name=None)]


def compare(day: dt.date, src: DukascopyTickSource) -> dict:
    saved = pd.read_parquet(day_parquet_path(day, data_dir=DATA_DIR))
    start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    refetch = src.fetch_ticks(start, start + dt.timedelta(days=1))
    if refetch is None:
        refetch = pd.DataFrame(columns=COLS)

    s, r = _normalize(saved), _normalize(refetch)
    st, rt = _tuples(s), _tuples(r)
    cs, cr = Counter(st), Counter(rt)

    # timestamp レベルで訂正 vs 追記/欠落を分離
    s_ts, r_ts = Counter(s["ts_ns"]), Counter(r["ts_ns"])
    added_ts = sum((r_ts - s_ts).values())   # 追記（新規 timestamp）
    removed_ts = sum((s_ts - r_ts).values())  # 欠落（消えた timestamp）

    s_by: dict[int, Counter] = {}
    for tup in st:
        s_by.setdefault(tup[0], Counter())[tup[1:]] += 1
    r_by: dict[int, Counter] = {}
    for tup in rt:
        r_by.setdefault(tup[0], Counter())[tup[1:]] += 1
    revised, examples = 0, []
    for t in set(s_by) & set(r_by):
        if s_by[t] != r_by[t]:
            revised += 1
            if len(examples) < 3:
                examples.append((int(t), dict(s_by[t]), dict(r_by[t])))

    return {
        "day": day.isoformat(), "saved_rows": len(s), "refetch_rows": len(r),
        "unchanged": sum((cs & cr).values()), "added_ts": added_ts,
        "removed_ts": removed_ts, "revised_ts": revised, "examples": examples,
    }


def main() -> int:
    offsets = [int(a) for a in sys.argv[1:]] or [1, 3, 7, 30]
    today = dt.datetime.now(dt.timezone.utc).date()
    days = _saved_days()
    src = DukascopyTickSource(instrument=JP225)
    targets: list[dt.date] = []
    for off in offsets:
        d = _pick(days, today, off)
        if d and d not in targets:
            targets.append(d)
    print(f"today(UTC)={today}  対象日: {[d.isoformat() for d in targets]}", flush=True)

    any_revised = False
    for d in targets:
        res = compare(d, src)
        revised = res["revised_ts"] > 0 or res["removed_ts"] > 0
        any_revised = any_revised or revised
        verdict = "★訂正/欠落 検出（可変）" if revised else "追記のみ/差なし（不変を支持）"
        print(f"  {d}: saved={res['saved_rows']} refetch={res['refetch_rows']} "
              f"unchanged={res['unchanged']} 追記={res['added_ts']} 欠落={res['removed_ts']} "
              f"訂正ts={res['revised_ts']} -> {verdict}", flush=True)
        for ex in res["examples"]:
            print(f"     例 ts={ex[0]}: saved={ex[1]} refetch={ex[2]}", flush=True)

    # 安定性: 最古対象日を 2 回再取得し、再取得同士の一致を見る（ベンダ応答の決定性）。
    if targets:
        d = targets[-1]
        start = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
        r1 = _tuples(_normalize(src.fetch_ticks(start, start + dt.timedelta(days=1))))
        r2 = _tuples(_normalize(src.fetch_ticks(start, start + dt.timedelta(days=1))))
        print(f"[安定性] {d} 2回再取得の一致: {r1 == r2}", flush=True)

    if any_revised:
        print("結論: 過去日に訂正/欠落を検出 → 『過去は不変』の前提は偽", flush=True)
        return 1
    print("結論: 訂正/欠落なし → 『過去は不変』の前提を実測で支持", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
