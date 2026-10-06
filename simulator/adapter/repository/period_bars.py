"""選んだ足（Period）の足を 1 分足から作る（2026-10-06・依頼者承認）。

選んだ足で判定する run は、MT5 と同じく**判定はチャートの足・約定はティック**で行う:
    * 戦略は選んだ足（日足など）が始まる 1 分足でだけ判定し、指標は選んだ足の系列で引く。
    * 約定・SL/TP・口座の評価は 1 分足（とその足の途中の評価点）で行う。

足の区切りは marketdata の再集計（`marketdata.resample.resample_ohlc_tf`）が唯一の所有者
であり、ここで書き直さない（日足以上はブローカー暦日＝MT5 のサーバ日・4h はセッション日起点・
他は UTC 床）。各期間の UTC 始端も同じモジュールの `period_utc_start` から引く。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from marketdata.resample import period_utc_start, resample_ohlc_tf
from simulator.usecase.bar_times import bar_epoch_seconds

#: 選んだ足の列（marketdata 形式の CSV に書く列・この並びで書く）。
_COLUMNS = ("open", "high", "low", "close", "volume", "spread")


def _epochs(bars: Sequence[Any]) -> np.ndarray:
    # 足の時刻は列ごとに一括で変換する（足ごとに変換しない・ISSUE-553 項目 3）。
    return np.asarray(bar_epoch_seconds(bars), dtype=np.int64)


def period_frame(m1_bars: Sequence[Any], tf: str) -> pd.DataFrame:
    """1 分足の Bar 列を ``tf`` の足へまとめる（区切りと列の集約規則は marketdata の宣言）。"""
    index = pd.to_datetime(_epochs(m1_bars), unit="s")
    frame = pd.DataFrame(
        {name: [getattr(b, name) for b in m1_bars] for name in _COLUMNS}, index=index
    )
    return resample_ohlc_tf(frame, tf)


def write_marketdata_csv(frame: pd.DataFrame, path: Path) -> Path:
    """まとめた足を marketdata 形式（先頭列 ``date``・naive UTC）の CSV に書く。"""
    out = frame.loc[:, list(_COLUMNS)].copy()
    out["spread"] = out["spread"].astype(int)
    out.insert(0, "date", out.index.strftime("%Y-%m-%d %H:%M:%S"))
    out.to_csv(path, index=False)
    return path


def decision_bars(
    m1_bars: Sequence[Any], period_bars: Sequence[Any], tf: str
) -> "list[int | None]":
    """1 分足ごとに「この足で判定する選んだ足の番号」を返す（判定しない足は None）。

    選んだ足 k の期間は ``[period_utc_start(k), period_utc_start(k+1))``。1 分足は自分の時刻が
    入る期間に属し、期間の**最初の** 1 分足でだけ判定する。どの期間にも入らない 1 分足
    （最初の選んだ足より前）は判定しない。
    """
    starts = np.fromiter(
        (
            int(period_utc_start(tf, pd.Timestamp(t, unit="s")).timestamp())
            for t in _epochs(period_bars).tolist()
        ),
        dtype=np.int64,
        count=len(period_bars),
    )
    owner = np.searchsorted(starts, _epochs(m1_bars), side="right") - 1
    out: "list[int | None]" = [None] * len(m1_bars)
    previous = -1
    for i, k in enumerate(owner.tolist()):
        if k >= 0 and k != previous:
            out[i] = k
        previous = k
    return out
