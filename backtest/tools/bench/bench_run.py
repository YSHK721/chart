"""Throwaway benchmark runner for tick-loading strategy PoC.

NOT production code. NOT tested (per approved PoC scope). Measures the *heavy
operations* the user cares about: parsed rows, files opened, wall time (median
of N), peak RSS. Compares:
  A. CSV full load + post-filter
  B. Parquet day-partitioned + pyarrow.dataset predicate pushdown (pruning)
  C. Parquet hour-partitioned (small-file overhead)
  D. column pruning (subset vs all columns)

Datasets are written to a temp dir and removed at the end. The generator
(synth_ticks.py) is kept; generated data is throwaway.

Usage:
  python -m backtest.tools.bench.bench_run --days 365 --tpd 10000 --repeat 3
"""
from __future__ import annotations

import argparse
import gc
import shutil
import statistics
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from backtest.tools.bench.synth_ticks import TickGenConfig, generate_ticks

_PAGE_MB = 4096 / 1e6  # resource.getpagesize() on Linux = 4096


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------
def _current_rss_mb() -> float:
    """Current resident set size in MB via /proc/self/statm (field 2 = pages)."""
    with open("/proc/self/statm") as f:
        rss_pages = int(f.read().split()[1])
    return rss_pages * _PAGE_MB


class _RssSampler:
    """Background thread sampling current RSS to capture the peak working set
    *during* a scenario (delta over baseline), since ru_maxrss is monotonic
    and cannot show per-scenario peaks."""

    def __init__(self, interval: float = 0.005):
        self.interval = interval
        self.peak = 0.0
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self.peak = _current_rss_mb()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def _poll(self):
        while not self._stop.is_set():
            cur = _current_rss_mb()
            if cur > self.peak:
                self.peak = cur
            time.sleep(self.interval)

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


def _time_median(fn, repeat: int):
    """Run fn `repeat` times. Return (median_seconds, rows, files, peak_delta_mb).

    peak_delta_mb = max(current RSS during the run) - (RSS just before the run),
    measured on the last repeat so prior allocations don't pollute the delta.
    """
    times = []
    last = None
    peak_delta = 0.0
    for i in range(repeat):
        gc.collect()
        baseline = _current_rss_mb()
        with _RssSampler() as sampler:
            t0 = time.perf_counter()
            last = fn()
            times.append(time.perf_counter() - t0)
        peak_delta = max(0.0, sampler.peak - baseline)
    rows = last["rows"]
    files = last["files"]
    return statistics.median(times), rows, files, peak_delta


# ---------------------------------------------------------------------------
# Dataset writers
# ---------------------------------------------------------------------------
def write_csv_monolithic(df: pd.DataFrame, path: Path) -> int:
    df.to_csv(path, index=False)
    return 1


def _add_partition_cols(df: pd.DataFrame) -> pd.DataFrame:
    ts = df["timestamp"]
    out = df.copy()
    out["year"] = ts.dt.year
    out["month"] = ts.dt.month
    out["day"] = ts.dt.day
    out["hour"] = ts.dt.hour
    return out


def write_parquet_partitioned(df: pd.DataFrame, root: Path, by_hour: bool) -> int:
    """Write year/month/day[/hour] partitioned parquet via pyarrow.dataset.

    Returns number of part files written.
    """
    part_cols = ["year", "month", "day"] + (["hour"] if by_hour else [])
    table = pa.Table.from_pandas(_add_partition_cols(df), preserve_index=False)
    ds.write_dataset(
        table,
        base_dir=str(root),
        format="parquet",
        partitioning=part_cols,
        partitioning_flavor="hive",
        existing_data_behavior="overwrite_or_ignore",
    )
    return sum(1 for _ in root.rglob("*.parquet"))


# ---------------------------------------------------------------------------
# Readers (each returns dict with rows + files actually opened/scanned)
# ---------------------------------------------------------------------------
def read_csv_filter(path: Path, lo: pd.Timestamp | None, hi: pd.Timestamp | None):
    df = pd.read_csv(path, parse_dates=["timestamp"])
    files = 1
    if lo is not None:
        df = df[(df["timestamp"] >= lo) & (df["timestamp"] < hi)]
    return {"rows": len(df), "files": files, "df": df}


def _date_predicate(lo: pd.Timestamp, hi: pd.Timestamp):
    """Build a hive-partition predicate on (year,month,day) covering [lo,hi)."""
    # Coarse partition-level predicate, then exact timestamp filter.
    days = []
    d = lo.normalize()
    while d < hi:
        days.append((d.year, d.month, d.day))
        d += timedelta(days=1)
    expr = None
    for (y, m, dd) in days:
        cond = (
            (ds.field("year") == y)
            & (ds.field("month") == m)
            & (ds.field("day") == dd)
        )
        expr = cond if expr is None else (expr | cond)
    return expr


def read_parquet_pruned(
    root: Path,
    lo: pd.Timestamp | None,
    hi: pd.Timestamp | None,
    columns: list[str] | None = None,
):
    dataset = ds.dataset(str(root), format="parquet", partitioning="hive")
    if lo is not None:
        part_expr = _date_predicate(lo, hi)
        # Count fragments actually selected (files opened after pruning).
        selected = list(dataset.get_fragments(filter=part_expr))
        files = len(selected)
        table = dataset.to_table(filter=part_expr, columns=columns)
        tdf = table.to_pandas()
        if "timestamp" in tdf.columns:
            tdf = tdf[(tdf["timestamp"] >= lo) & (tdf["timestamp"] < hi)]
        rows = len(tdf)
    else:
        files = len(list(dataset.get_fragments()))
        table = dataset.to_table(columns=columns)
        rows = table.num_rows
    return {"rows": rows, "files": files}


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def run(days: int, tpd: int, repeat: int):
    workdir = Path(tempfile.mkdtemp(prefix="tickbench_"))
    results = []
    try:
        cfg = TickGenConfig(start_date=date(2025, 1, 1), days=days, ticks_per_day=tpd)
        total_rows = cfg.total_rows()
        print(f"[gen] {total_rows:,} rows ({days}d x {tpd}/d)...", flush=True)
        df = generate_ticks(cfg)

        csv_path = workdir / "ticks.csv"
        pq_day_root = workdir / "pq_day"
        pq_hour_root = workdir / "pq_hour"

        print("[write] csv...", flush=True)
        write_csv_monolithic(df, csv_path)
        print("[write] parquet day...", flush=True)
        n_day_files = write_parquet_partitioned(df, pq_day_root, by_hour=False)
        print("[write] parquet hour...", flush=True)
        n_hour_files = write_parquet_partitioned(df, pq_hour_root, by_hour=True)

        csv_size = csv_path.stat().st_size / 1e6
        pq_day_size = sum(p.stat().st_size for p in pq_day_root.rglob("*.parquet")) / 1e6
        pq_hour_size = sum(p.stat().st_size for p in pq_hour_root.rglob("*.parquet")) / 1e6
        print(
            f"[size] csv={csv_size:.1f}MB pq_day={pq_day_size:.1f}MB "
            f"({n_day_files} files) pq_hour={pq_hour_size:.1f}MB ({n_hour_files} files)",
            flush=True,
        )

        start = pd.Timestamp("2025-01-01")
        sub_1d = (start, start + pd.Timedelta(days=1))
        sub_1w = (start, start + pd.Timedelta(days=7))
        sub_1m = (start, start + pd.Timedelta(days=30))

        def measure(name, fn):
            t, rows, files, peak_delta = _time_median(fn, repeat)
            results.append((name, t, peak_delta, rows, files))
            print(
                f"[done] {name}: t={t*1000:.0f}ms dRSS={peak_delta:.0f}MB "
                f"rows={rows:,} files={files}",
                flush=True,
            )

        # Scenario 1: full read A vs B
        measure("S1 full CSV", lambda: read_csv_filter(csv_path, None, None))
        measure("S1 full PQ(day)", lambda: read_parquet_pruned(pq_day_root, None, None))

        # Scenario 2: sub-range read A(full+filter) vs B(prune)
        for label, (lo, hi) in [("1d", sub_1d), ("1w", sub_1w), ("1m", sub_1m)]:
            measure(f"S2 {label} CSV", lambda lo=lo, hi=hi: read_csv_filter(csv_path, lo, hi))
            measure(
                f"S2 {label} PQ(day)",
                lambda lo=lo, hi=hi: read_parquet_pruned(pq_day_root, lo, hi),
            )

        # Scenario 3: granularity B(day) vs C(hour) on same sub-range (1d)
        lo, hi = sub_1d
        measure("S3 1d PQ(day)", lambda: read_parquet_pruned(pq_day_root, lo, hi))
        measure("S3 1d PQ(hour)", lambda: read_parquet_pruned(pq_hour_root, lo, hi))

        # Scenario 4: format decode on identical full rows (CSV parse vs PQ read)
        measure("S4 decode CSV(full)", lambda: read_csv_filter(csv_path, None, None))
        measure("S4 decode PQ(full)", lambda: read_parquet_pruned(pq_day_root, None, None))

        # Scenario 5: column pruning (1m range, subset cols vs all)
        lo, hi = sub_1m
        measure(
            "S5 1m PQ all-cols",
            lambda: read_parquet_pruned(pq_day_root, lo, hi, columns=None),
        )
        measure(
            "S5 1m PQ 2-cols",
            lambda: read_parquet_pruned(
                pq_day_root, lo, hi, columns=["timestamp", "last"]
            ),
        )

        print("\n=== RESULTS (median of {} runs) ===".format(repeat))
        print(f"{'scenario':<22}{'time(ms)':>10}{'dRSS(MB)':>11}{'rows':>12}{'files':>8}")
        for name, t, peak, rows, files in results:
            print(f"{name:<22}{t*1000:>10.0f}{peak:>11.0f}{rows:>12,}{files:>8}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"\n[cleanup] removed {workdir}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--tpd", type=int, default=10000, help="ticks per day")
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()
    run(args.days, args.tpd, args.repeat)
