"""tf_period_profile — 時間足毎の最小価格単位プロファイル列（ローリング窓配信の生成本体）。

「時間足毎のprofile列」機能の backend 生成。tick(mid) を **選択 tf 周期**（``floor(t, tf_sec)``）で分割し、
各周期を**最小価格単位**（``unit``＝銘柄の最小価格刻み・mid 解像度）でビニングして sparse な占有レベル列
（``[[price, count]...]``）と POC/VA を返す。配信は**ローリング窓** ``[from_unix, to_unix)`` 内の周期のみ
（列数を有界化し応答肥大を防ぐ）。純関数（I/O 無し）。

実測（.doc/PROFILE_MICRO_STRUCTURE_VERIFICATION.md）で短周期でも分布が成立することを確認済み＝
1m でも min-unit で意味のある占有レベルが得られる。空ゼロは送らない（sparse）ため応答は占有数に比例。
"""
from __future__ import annotations

from typing import Any

import numpy as np


def _value_area_sparse(counts: np.ndarray, poc_i: int, va_pct: float) -> tuple[int, int]:
    """占有レベル（価格昇順の ``counts``）で POC から拡張し、累積が ``va_pct`` 到達までの [lo, hi] index。

    標準 Market Profile の VA 拡張（POC を起点に、隣接の TPO が大きい側へ広げる）。占有レベル上で行う
    （min-unit の空ギャップは 0 なので寄与せず、拡張の到達 index は price レンジと一致する）。
    """
    total = int(counts.sum())
    if total <= 0:
        return poc_i, poc_i
    target = va_pct * total
    lo = hi = poc_i
    acc = int(counts[poc_i])
    n = len(counts)
    while acc < target and (lo > 0 or hi < n - 1):
        down = int(counts[lo - 1]) if lo > 0 else -1
        up = int(counts[hi + 1]) if hi < n - 1 else -1
        if up >= down:
            hi += 1
            acc += int(counts[hi])
        else:
            lo -= 1
            acc += int(counts[lo])
    return lo, hi


def tf_period_profiles(
    secs: Any,
    mids: Any,
    tf_sec: int,
    unit: float,
    from_unix: int,
    to_unix: int,
    va_pct: float = 0.70,
) -> list[dict]:
    """tick を tf 周期で分割し、各周期を最小価格単位でビニングした sparse プロファイル列を返す。

    Args:
        secs: tick の UNIX 秒（配列）。 mids: tick の mid 価格（配列・同順）。
        tf_sec: 周期秒（例 1m=60）。 unit: 最小価格単位（>0）。
        from_unix, to_unix: ローリング窓（周期始端が ``[from, to)`` の周期のみ返す）。
        va_pct: バリューエリア割合（既定 0.70）。

    Returns:
        価格始端時刻昇順の列 ``[{time, levels:[[price,count]...], poc, va_low, va_high,
        price_min, price_max, tpo_units}]``。``levels`` は占有レベルのみ（価格昇順・sparse）。
    """
    secs = np.asarray(secs)
    mids = np.asarray(mids, dtype=float)
    if secs.size == 0:
        return []
    tf_sec = int(tf_sec)
    unit = float(unit)
    period = (secs.astype(np.int64) // tf_sec) * tf_sec
    mask = (period >= int(from_unix)) & (period < int(to_unix))
    if not mask.any():
        return []
    period = period[mask]
    lvl = np.round(mids[mask] / unit).astype(np.int64)  # 最小単位で量子化したレベル index。

    order = np.argsort(period, kind="stable")
    period_s = period[order]
    lvl_s = lvl[order]
    uniq, starts = np.unique(period_s, return_index=True)
    bounds = list(starts) + [len(period_s)]

    out: list[dict] = []
    for k, pstart in enumerate(uniq):
        seg = lvl_s[bounds[k] : bounds[k + 1]]
        levs, counts = np.unique(seg, return_counts=True)  # 価格昇順（level index 昇順）。
        prices = levs * unit
        poc_i = int(counts.argmax())  # 同値は先頭（最安値側）。
        lo, hi = _value_area_sparse(counts, poc_i, va_pct)
        out.append(
            {
                "time": int(pstart),
                "levels": [[round(float(p), 4), int(c)] for p, c in zip(prices, counts)],
                "poc": round(float(prices[poc_i]), 4),
                "va_low": round(float(prices[lo]), 4),
                "va_high": round(float(prices[hi]), 4),
                "price_min": round(float(prices[0]), 4),
                "price_max": round(float(prices[-1]), 4),
                "tpo_units": int(counts.sum()),
            }
        )
    return out
