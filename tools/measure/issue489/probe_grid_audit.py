"""段階 1: 4h グリッド 3 方式の構造監査（ISSUE-489・PLAN.md M-1〜M-5）。読み取り専用。

アンカーの実測根拠（2026-09-05・本 probe 冒頭でも毎回検証する）:
    jp225_tick（Dukascopy）の開場は夏 22:00 / 冬 23:00 UTC・切替は米国 DST
    ＝**ブローカー日境界は 18:00 America/New_York**（月別実測: 1〜2 月と 12 月は全開場 23 時・
    4〜10 月の主開場 22 時・3 月と 11 月で切替。23 時側の少数は日曜再開場が 1 時間遅いもの）。
    jp225_mt5（OANDA）はサーバ時刻 EET/EEST（ISSUE-447 T1b）＝**日境界は 00:00 Europe/Athens**。
"""
from __future__ import annotations

import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "marketdata"

#: 実休場の判定（実測: 日次休場 >=100 分・閑散無ティック帯は 2019 年以降最長 83 分）。
BREAK_MINUTES = 100

SINCE = "2019-01-01"


def load_m1(path: Path, since: "str | None" = None) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    if since:
        df = df[df.index >= since]
    return df


def session_ids(index: pd.DatetimeIndex) -> np.ndarray:
    gap = index.to_series().diff().dt.total_seconds() > BREAK_MINUTES * 60
    return gap.cumsum().to_numpy()


def _unix_seconds(index: pd.DatetimeIndex) -> np.ndarray:
    """UNIX 秒（解像度非依存・gateway と同じ符号化）。"""
    return index.tz_localize(None).values.astype("datetime64[s]").astype("int64") \
        if index.tz is not None else index.values.astype("datetime64[s]").astype("int64")


def broker_day_starts(index: pd.DatetimeIndex, tz: str, boundary_hour: int) -> np.ndarray:
    """各行が属するブローカー日の始端（UTC unix 秒）。境界＝現地 ``boundary_hour`` 時。"""
    local = index.tz_localize("UTC").tz_convert(ZoneInfo(tz))
    day = (local - pd.Timedelta(hours=boundary_hour)).normalize()
    start = (day + pd.Timedelta(hours=boundary_hour)).tz_convert("UTC")
    return _unix_seconds(start)


def anchored_starts(index: pd.DatetimeIndex, minutes: int, day_start: np.ndarray) -> np.ndarray:
    ts = _unix_seconds(index)
    slot = (ts - day_start) // (minutes * 60)
    return day_start + slot * minutes * 60


def audit(df: pd.DataFrame, label: str, bar_starts: np.ndarray, day_start: np.ndarray,
          bar_minutes: int) -> None:
    sid = session_ids(df.index)
    g = pd.DataFrame({
        "bar": bar_starts, "sid": sid, "slot": (bar_starts - day_start) // (bar_minutes * 60),
        "open": df["open"].to_numpy(), "close": df["close"].to_numpy(),
        "volume": df["volume"].to_numpy(),
        "high": df["high"].to_numpy(), "low": df["low"].to_numpy(),
    }, index=df.index)
    per_bar = g.groupby("bar").agg(sids=("sid", "nunique"), slot=("slot", "first"),
                                   volume=("volume", "sum"),
                                   high=("high", "max"), low=("low", "min"))
    mixed = int((per_bar["sids"] > 1).sum())
    total = len(per_bar)

    hidden = []
    for _bar, chunk in g.groupby("bar"):
        d = chunk.index.to_series().diff().dt.total_seconds().to_numpy()
        for i in np.nonzero(d > BREAK_MINUTES * 60)[0]:
            hidden.append(abs(chunk["open"].iloc[i] - chunk["close"].iloc[i - 1]))

    med = per_bar.groupby("slot")["volume"].median()
    rng = per_bar.groupby("slot").apply(lambda x: (x["high"] - x["low"]).median())
    cv = float(med.std() / med.mean())
    print(f"{label}: バー {total} / セッション混在 {mixed} 本 / "
          f"隠蔽ギャップ {len(hidden)} 回 (平均 {np.mean(hidden) if hidden else 0:.1f} / "
          f"最大 {np.max(hidden) if hidden else 0:.1f} 円) / スロット出来高CV {cv:.3f}")
    print(f"    スロット別 出来高中央値: {med.round(0).to_dict()}")
    print(f"    スロット別 レンジ中央値: {rng.round(1).to_dict()}")


def session_totals_direct(df: pd.DataFrame) -> pd.DataFrame:
    sid = session_ids(df.index)
    g = df.assign(sid=sid).groupby("sid")
    return pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(), "volume": g["volume"].sum(),
    })


def session_totals_via_grid(df: pd.DataFrame, bar_starts: np.ndarray) -> pd.DataFrame:
    sid = session_ids(df.index)
    bars = df.assign(bar=bar_starts, sid=sid).groupby(["sid", "bar"]).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"))
    s = bars.groupby(level=0)
    return pd.DataFrame({
        "open": s["open"].first(), "high": s["high"].max(),
        "low": s["low"].min(), "close": s["close"].last(), "volume": s["volume"].sum(),
    })


def main() -> None:
    dukas_m1 = load_m1(DATA / "jp225_tick_m1.csv", since=SINCE)
    print(f"jp225_tick M1: {dukas_m1.index.min()} .. {dukas_m1.index.max()}  rows={len(dukas_m1)}")

    # アンカー仮説の検証: 開場は常にブローカー日境界（18:00 NY）から 0 または 60 分後（日曜等）か
    day_start = broker_day_starts(dukas_m1.index, "America/New_York", 18)
    sid = session_ids(dukas_m1.index)
    opens = pd.Series(_unix_seconds(dukas_m1.index), index=dukas_m1.index).groupby(sid).min()
    ds_of_open = pd.Series(day_start, index=dukas_m1.index).groupby(sid).first()
    lag_min = ((opens - ds_of_open) // 60)
    dist = lag_min.value_counts().sort_values(ascending=False).head(6)
    within = float((lag_min.isin([0, 60])).mean()) * 100
    print(f"開場と日境界(18:00 NY)の差（分・上位）: {dist.to_dict()} → 0 or 60 分が {within:.1f}%")

    ts = dukas_m1.index
    grids = [
        ("(a) UTC床 4h", _unix_seconds(ts.floor("4h")), 240),
        ("(b) MT5式 4h(日境界起点)", anchored_starts(ts, 240, day_start), 240),
        ("(c) 均等 267m(日境界起点)", anchored_starts(ts, 267, day_start), 267),
    ]
    print(f"\n== 監査（jp225_tick {SINCE} 以降）==")
    for label, starts, minutes in grids:
        # (a) のスロットは同じ日境界基準で数える（比較の物差しを揃える）
        audit(dukas_m1, label, np.asarray(starts), day_start, minutes)

    print("\n== M-4 セッション畳み一致 ==")
    base = session_totals_direct(dukas_m1)
    for label, starts, _m in grids:
        via = session_totals_via_grid(dukas_m1, np.asarray(starts))
        ok = np.allclose(base.to_numpy(), via.to_numpy(), rtol=0, atol=1e-9)
        print(f"{label}: {'完全一致' if ok else '不一致!!'}")

    # M-5: MT5 素材（日境界 = 00:00 Europe/Athens）
    mt5 = load_m1(DATA / "jp225_mt5_m1.csv", since=SINCE)
    day5 = broker_day_starts(mt5.index, "Europe/Athens", 0)
    starts5 = anchored_starts(mt5.index, 240, day5)
    print(f"\n== M-5 jp225_mt5（{mt5.index.min()}〜）へ (b) サーバ日起点 4h ==")
    audit(mt5, "(b) on MT5素材", np.asarray(starts5), day5, 240)
    g5 = pd.DataFrame({"bar": starts5, "day": day5}, index=mt5.index)
    minutes_per_slot = g5.groupby("bar").size()
    slot5 = (g5.groupby("bar")["bar"].first() - g5.groupby("bar")["day"].first()) // (240 * 60)
    per_slot_minutes = minutes_per_slot.groupby(slot5).median()
    print(f"    スロット別 取引分数の中央値: {per_slot_minutes.to_dict()}")


if __name__ == "__main__":
    main()
