"""tick_window — 実ティック窓読み込み（leaf・pandas のみ依存）。

proto_server.do_intraday の tick ロジックを抽出した唯一の実装源。
``window_ticks(start, end)`` は足の期間 ``[start, end)`` を跨ぐ全 UTC 日の parquet を走査し、
timestamp で窓フィルタ → ``mid=(bid+ask)/2`` → 窓内 mid 中央値 ±threshold 外れ値除去を行い、
``[(sec, mid), ...]`` を時系列順で返す。外れ値式・窓フィルタは proto_server 現行実装と bit 一致。

proto_server（viz）と engine（分析）の双方がここを import する（循環なし）。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

# 実ティック parquet 根（read-only）。proto_server の TICK_ROOT（prototype_dir.parent/data/...）と同一物理位置。
#   contact_scan/ から見ると repo 根は parents[2]。
TICK_ROOT = Path(__file__).resolve().parents[2] / "data" / "marketdata" / "ticks"

# 外れ値補正の許容相対乖離（0.3=±30%）。proto_server / tools.export_jp225_m1 と同一基準。
OUTLIER_THRESHOLD = 0.3


def window_ticks(start: int, end: int, *, threshold: float = OUTLIER_THRESHOLD):
    """足の期間 ``[start, end)``（UNIX 秒）の実ティックを ``[(sec, mid), ...]`` で返す。

    proto_server.do_intraday の tick ブロックを移植したもの。返り値の mid 列（2 要素目のみ取り出した
    もの）は現行 ``do_intraday`` の ``out["ticks"]`` と bit 一致する（cap なし・間引かない・接点検証用）。
    parquet が 1 ファイルも無い窓では空リストを返す。
    """
    frames = []
    d0 = datetime.fromtimestamp(start, tz=timezone.utc).date()
    d1 = datetime.fromtimestamp(max(start, end - 1), tz=timezone.utc).date()
    day = d0
    while day <= d1:
        p = TICK_ROOT / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" / "JP225_ticks.parquet"
        if p.is_file():
            frames.append(pd.read_parquet(p, columns=["timestamp", "bidPrice", "askPrice"]))
        day += timedelta(days=1)
    if not frames:
        return []
    tdf = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    # timestamp(UTC, tz-aware) → 秒（m1 と同基準: tz を外して datetime64[s]→int64）。
    secs = tdf["timestamp"].dt.tz_localize(None).values.astype("datetime64[s]").astype("int64")
    win = (secs >= start) & (secs < end)
    tdf = tdf[win]
    sec_arr = secs[win]                                  # tdf の各行と位置対応（窓内のみ）
    mid = (tdf["bidPrice"] + tdf["askPrice"]) / 2.0      # tdf.index 付き Series
    # 生ティックも外れ値補正（生 parquet は不変・読み取り時のみ）。窓内 mid 中央値から ±threshold 超を除去。
    m = float(mid.median()) if len(mid) else 0.0
    mid_vals = mid.values
    if m > 0:
        keep = ((mid / m - 1.0).abs() <= threshold).values  # 位置基準の bool（sec_arr と整合）
        mid_vals = mid_vals[keep]
        sec_arr = sec_arr[keep]
    return [(int(s), float(v)) for s, v in zip(sec_arr.tolist(), mid_vals)]
