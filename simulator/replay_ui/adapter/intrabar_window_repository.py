"""IntrabarWindowRepository — /intraday の IntrabarWindowPort 実装（proto do_intraday 忠実）。

m1  : 区間 [start,end) の 1 分足 OHLC 行（``[o,h,l,c]``）。供給は **dataset の単一権威**
      ``dataset.load_atom_window``（全期間原子・clamp 外れ値補正・mtime キャッシュ・ISSUE-132）へ
      完全委譲する（旧: 生 CSV 全読み＋独自 repair＋独自キャッシュの第二経路を全廃）。上位足の
      ペイロードは ``_cap_m1_rows`` で 1500 行へ間引く（先頭/末尾＋窓内 高値最大/安値最小は必ず残す）。
ticks: 区間 [start,end) の実ティック ``(sec, mid)``。tick parquet（Y/M/D）を [start,end) 跨ぎで走査し
      ``(sec, bid, ask)`` を組み、**domain E-4 ``tick_mid_series.mid_series``** で mid 算出＋窓フィルタ
      ＋中央値外れ値除去を行う（cap 無し・接点検証の絶対仕様）。tick_window.window_ticks と bit 一致。

技術隔離（CLEAN_ARCH §6）: pandas / parquet IO は本ファイル内に閉じる。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from simulator.replay_ui.adapter import _indicator_ui_bridge
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
    """IntrabarWindowPort 実装。m1 は dataset 委譲・tick は parquet 直読（リプレイ固有フィード）。"""

    def __init__(
        self,
        tick_root: Any,
        api_path: Any = None,
        repo_root: Any = None,
        m1_cap: int = _M1_CAP,
        outlier_threshold: float = OUTLIER_THRESHOLD,
        bridge_loader: "Callable[..., Any] | None" = None,
    ) -> None:
        self._tick_root = Path(tick_root)
        self._api_path = api_path
        self._repo_root = repo_root
        self._m1_cap = m1_cap
        self._threshold = outlier_threshold  # tick mid 系列の中央値外れ値除去（domain E-4）用。
        # 既定は実 bridge の load。テストは fake loader を注入（MarketProfileGateway と同型）。
        self._loader = bridge_loader if bridge_loader is not None else _indicator_ui_bridge.load

    # ---- IntrabarWindowPort ----

    def load_m1_rows(self, ref: str, start: int, end: int) -> "list[list[float]]":
        bridge = self._loader(self._api_path, self._repo_root)
        sub = bridge.dataset.load_atom_window(ref, start, end)
        rows = [
            [float(r.open), float(r.high), float(r.low), float(r.close)]
            for r in sub.itertuples(index=False)
        ]
        return _cap_m1_rows(rows, self._m1_cap)

    def load_ticks(self, start: int, end: int) -> "list[tuple[int, float]]":
        raw = self._load_raw_ticks(start, end)
        return mid_series(raw, start, end, threshold=self._threshold)

    # ---- internal ----

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
