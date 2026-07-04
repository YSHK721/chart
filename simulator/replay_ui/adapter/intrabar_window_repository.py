"""IntrabarWindowRepository — /intraday の IntrabarWindowPort 実装（proto do_intraday 忠実）。

m1  : 区間 [start,end) の 1 分足 OHLC 行（``[o,h,l,c]``）。上位足のペイロードは ``_cap_m1_rows`` で
      1500 行へ間引く（先頭/末尾＋窓内 高値最大/安値最小の行は必ず残す）。1D 以下は無変更。
ticks: 区間 [start,end) の実ティック ``(sec, mid)``。tick parquet（Y/M/D）を [start,end) 跨ぎで走査し
      ``(sec, bid, ask)`` を組み、**domain E-4 ``tick_mid_series.mid_series``** で mid 算出＋窓フィルタ
      ＋中央値外れ値除去を行う（cap 無し・接点検証の絶対仕様）。tick_window.window_ticks と bit 一致。

技術隔離（CLEAN_ARCH §6）: pandas / parquet IO は本ファイル内に閉じる。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from simulator.replay_ui.adapter import _indicator_ui_bridge
from simulator.replay_ui.adapter._m1_repair import repair_day_outliers
from simulator.replay_ui.domain.tick_mid_series import OUTLIER_THRESHOLD, mid_series

_M1_CAP = 1500


def _cap_m1_rows(rows: "list[list[float]]", n: int) -> "list[list[float]]":
    """m1 OHLC 行を最大 n 行へ間引く（proto _cap_m1_rows と bit 一致）。

    先頭/末尾＋窓内 高値最大(idx1)/安値最小(idx2) の行を必ず残す。1D 以下（≤n）は無変更。
    """
    if len(rows) <= n:
        return rows
    i_hi = max(range(len(rows)), key=lambda i: rows[i][1])  # high 最大
    i_lo = min(range(len(rows)), key=lambda i: rows[i][2])  # low 最小
    keep = {0, len(rows) - 1, i_hi, i_lo}
    stride = len(rows) / n
    for k in range(n):
        keep.add(int(k * stride))
    return [rows[i] for i in sorted(keep)]


class IntrabarWindowRepository:
    """IntrabarWindowPort 実装。"""

    def __init__(
        self,
        tick_root: Any,
        tick_m1_csv: Any = None,
        api_path: Any = None,
        repo_root: Any = None,
        m1_cap: int = _M1_CAP,
        outlier_threshold: float = OUTLIER_THRESHOLD,
        m1_repair: bool = True,
    ) -> None:
        self._tick_root = Path(tick_root)
        self._tick_m1_csv = Path(tick_m1_csv) if tick_m1_csv is not None else None
        self._api_path = api_path
        self._repo_root = repo_root
        self._m1_cap = m1_cap
        self._threshold = outlier_threshold
        self._m1_repair = m1_repair
        self._m1_cache: dict = {}  # jp225_tick M1 の mtime キャッシュ（/intraday 連続でも再解析しない）

    # ---- IntrabarWindowPort ----

    def load_m1_rows(self, ref: str, start: int, end: int) -> "list[list[float]]":
        df = self._load_m1_df(ref)
        secs = df.index.values.astype("datetime64[s]").astype("int64")
        sub = df[(secs >= start) & (secs < end)]
        rows = [
            [float(r.open), float(r.high), float(r.low), float(r.close)]
            for r in sub.itertuples(index=False)
        ]
        return _cap_m1_rows(rows, self._m1_cap)

    def load_ticks(self, start: int, end: int) -> "list[tuple[int, float]]":
        raw = self._load_raw_ticks(start, end)
        return mid_series(raw, start, end, threshold=self._threshold)

    # ---- internal ----

    def _load_m1_df(self, ref: str) -> "pd.DataFrame":
        if ref == "jp225_tick":
            if self._tick_m1_csv is None:
                raise ValueError("tick_m1_csv 未設定（jp225_tick の m1 源が無い）")
            # mtime キャッシュ: /intraday 連続（足内アニメ再生）で CSV 再解析＋補正を繰り返さない
            #   （proto _load_tick_m1 と同じ・CausalCandleRepository と対称）。
            mt = self._tick_m1_csv.stat().st_mtime
            if self._m1_cache.get("mt") != mt:
                df = pd.read_csv(self._tick_m1_csv, parse_dates=["date"]).set_index("date")
                if self._m1_repair:
                    df = repair_day_outliers(df, self._threshold)
                self._m1_cache.update(mt=mt, df=df)
            return self._m1_cache["df"]
        bridge = _indicator_ui_bridge.load(self._api_path, self._repo_root)
        return bridge.dataset.load_dataframe(ref, "1m")

    def _load_raw_ticks(self, start: int, end: int) -> "list[tuple[int, float, float]]":
        """[start,end) を跨ぐ全 UTC 日の parquet から ``(sec, bid, ask)`` を組む（窓フィルタは domain）。

        tick_window.window_ticks と同一の日走査・秒符号化。窓外/外れ値の除去は
        ``tick_mid_series.mid_series`` に一元化する（bit 一致）。
        """
        frames: "list[pd.DataFrame]" = []
        d0 = datetime.fromtimestamp(start, tz=timezone.utc).date()
        d1 = datetime.fromtimestamp(max(start, end - 1), tz=timezone.utc).date()
        day = d0
        while day <= d1:
            p = (
                self._tick_root
                / f"{day.year:04d}"
                / f"{day.month:02d}"
                / f"{day.day:02d}"
                / "JP225_ticks.parquet"
            )
            if p.is_file():
                frames.append(pd.read_parquet(p, columns=["timestamp", "bidPrice", "askPrice"]))
            day += timedelta(days=1)
        if not frames:
            return []
        tdf = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        secs = tdf["timestamp"].dt.tz_localize(None).values.astype("datetime64[s]").astype("int64")
        bid = tdf["bidPrice"].tolist()
        ask = tdf["askPrice"].tolist()
        return [(int(secs[i]), float(bid[i]), float(ask[i])) for i in range(len(tdf))]
