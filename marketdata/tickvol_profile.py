"""tickvol_profile — 取引密度（ティック数）の「セッション日内・時刻帯」プロファイル。

一日の取引時間のうちどの時刻帯にティックが集中するかを、過去 N セッション日から求める。
背景色ハイライト（1 時間足以下）の帯定義の唯一源。

定義（決定論的・依頼者確定 2026-08-01）:
    ビン   : ``off = t - session_day_start(t)`` を :data:`BIN_SEC` (900 秒＝15 分) で量子化する。
             ビン幅は**表示時間足と独立の固定値**。
    集計   : 対象日の**直前** N セッション日（対象日は含まない＝因果窓）について、各日・各ビンの
             tickvol を合計し、そのビンの「日をまたいだ中央値」を代表値とする。
    判定   : 代表値の分布の ``pct`` パーセンタイル以上のビンを HIGH とする。
    帯     : 連続する HIGH ビンを結合し ``[start_off, end_off)`` へ圧縮する。

設計判断の実測根拠（2026-08-01・jp225_tick 実データ）:
    - 密度は時刻帯で最大 55 倍の差があり構造化されている（東京寄り付き・NY 序盤）。
    - 帯の位置は直近 3 か月の互いに素な 20 セッション窓どうしで Jaccard 0.84（安定）だが、
      9 か月離れると 0.33（別物）。よって**ローリング直近窓**が必須で、長い窓（60 セッション）は
      むしろ劣化する（0.44）。既定 N=20 はこの実測に基づく。
    - HIGH 判定の**絶対** tickvol 閾値は窓により 4861→9122 と 2 倍ドリフトする（市場全体の活況度が
      動く）。よって閾値はプロファイル内パーセンタイル（相対）でなければならない。
    - ビン幅を表示足のバー長にすると 1 分足で帯が 27 本へ断片化し背景が縞模様になる。15 分固定で
      帯は 4 本に収まり、かつ 1 時間ビン（0.81）と同等の安定性（0.84）を保つ。
    - 各日の合計での正規化は HIGH 一致率をほぼ変えない（0.40→0.41）ため採用しない。

依存方向: numpy / pandas と :mod:`marketdata.session_day` のみ（scipy はプロジェクト方針で禁止）。
I/O を持たない純関数＝サーバ層（ライブ・リプレイ）双方から同一実装で呼べる。
"""
from __future__ import annotations

from typing import Any

import numpy as np

from marketdata.session_day import session_day_start, session_day_starts

# 集計ビン幅（秒）。表示時間足と独立の固定値（上記実測 4 参照）。
BIN_SEC = 900
# 既定の集計セッション日数と HIGH 判定パーセンタイル。
DEFAULT_SESSIONS = 20
DEFAULT_PCT = 75
# 集計セッション数の上限。1 分足原子の供給 tail は 50,000 行（marketdata/serving_cache.py）で
#   1 セッション ≒ 1,300 行（実測）＝ 25 セッションで ≒32,500 行。これを超えると窓が履歴切れで
#   静かに短くなるため、仕様として上限を固定する（黙って精度が落ちるのを防ぐ）。
MAX_SESSIONS = 25
MIN_SESSIONS = 5


def clamp_sessions(n: "int | None") -> int:
    """集計セッション日数を [MIN_SESSIONS, MAX_SESSIONS] へ収める（None/非数は既定）。"""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return DEFAULT_SESSIONS
    return max(MIN_SESSIONS, min(MAX_SESSIONS, v))


def clamp_pct(p: "float | None") -> float:
    """HIGH 判定パーセンタイルを [50, 95] へ収める（None/非数は既定）。"""
    try:
        v = float(p)
    except (TypeError, ValueError):
        return float(DEFAULT_PCT)
    return max(50.0, min(95.0, v))


def _volume_column(df: Any) -> "str | None":
    """volume 列名を大文字小文字非依存で解決する（無ければ None）。"""
    for c in df.columns:
        if str(c).lower() in ("volume", "vol"):
            return str(c)
    return None


def session_offset_profile(
    df: Any,
    *,
    bin_sec: int = BIN_SEC,
    sessions: int = DEFAULT_SESSIONS,
    until: "int | float | None" = None,
) -> dict[str, Any]:
    """セッション日内オフセット別の tickvol 代表値（中央値）を返す。

    Args:
        df: DatetimeIndex（UTC 相当の naive）と volume 列を持つ 1 分足 DataFrame。
        bin_sec: ビン幅（秒）。
        sessions: 集計セッション日数（``until`` の属するセッション日は**含まない**）。
        until: 因果カットオフ（UNIX 秒）。None は「全データの末尾まで」＝ライブの現在。

    Returns:
        ``{"bin_sec": int, "sessions": int, "values": {off_index: median}, "day_count": int}``。
        データが無い場合は ``values={}``・``day_count=0``（呼び出し側は帯なしとして扱う）。
    """
    if df is None or len(df) == 0:
        return {"bin_sec": int(bin_sec), "sessions": 0, "values": {}, "day_count": 0}
    col = _volume_column(df)
    if col is None:
        return {"bin_sec": int(bin_sec), "sessions": 0, "values": {}, "day_count": 0}

    t = np.asarray(df.index.astype("datetime64[s]")).astype(np.int64)
    v = np.asarray(df[col], dtype="float64")
    finite = np.isfinite(v)
    if not finite.all():
        t, v = t[finite], v[finite]
    if t.size == 0:
        return {"bin_sec": int(bin_sec), "sessions": 0, "values": {}, "day_count": 0}

    starts = session_day_starts(t)
    # 因果: until が属するセッション日**より前**の日だけを使う（当日は含まない）。
    if until is not None:
        cutoff = session_day_start(until)
        keep = starts < cutoff
        t, v, starts = t[keep], v[keep], starts[keep]
        if t.size == 0:
            return {"bin_sec": int(bin_sec), "sessions": 0, "values": {}, "day_count": 0}

    days = np.unique(starts)[-int(sessions):]
    keep = np.isin(starts, days)
    t, v, starts = t[keep], v[keep], starts[keep]

    off = (t - starts) // int(bin_sec)
    # (日, ビン) で合計 → ビンごとに日をまたいだ中央値。日インデックスは days 内の順位。
    day_idx = np.searchsorted(days, starts)
    n_bins = int(off.max()) + 1 if off.size else 0
    sums = np.zeros((days.size, n_bins), dtype="float64")
    np.add.at(sums, (day_idx, off.astype(np.int64)), v)
    # その日にそのビンが存在しない（休場・短縮セッション）場合は中央値の母集団から外す。
    present = np.zeros((days.size, n_bins), dtype=bool)
    present[(day_idx, off.astype(np.int64))] = True

    values: dict[int, float] = {}
    for b in range(n_bins):
        col_vals = sums[present[:, b], b]
        if col_vals.size:
            values[b] = float(np.median(col_vals))
    return {
        "bin_sec": int(bin_sec),
        "sessions": int(days.size),
        "values": values,
        "day_count": int(days.size),
    }


def concentration_bands(
    values: dict[int, float], *, bin_sec: int = BIN_SEC, pct: float = DEFAULT_PCT
) -> list[dict[str, int]]:
    """ビン代表値から HIGH 帯（``[start_off, end_off)`` 秒）のリストを返す。

    閾値はプロファイル内パーセンタイル（相対）＝活況度の長期ドリフトに影響されない。
    連続する HIGH ビンは 1 本の帯へ結合する。
    """
    if not values:
        return []
    arr = np.asarray(list(values.values()), dtype="float64")
    threshold = float(np.percentile(arr, float(pct)))
    high = sorted(b for b, x in values.items() if x >= threshold)
    bands: list[dict[str, int]] = []
    for b in high:
        if bands and b == bands[-1]["_last"] + 1:
            bands[-1]["_last"] = b
            bands[-1]["endOff"] = (b + 1) * int(bin_sec)
            continue
        bands.append({"startOff": b * int(bin_sec), "endOff": (b + 1) * int(bin_sec), "_last": b})
    for band in bands:
        band.pop("_last", None)
    return bands


def profile_threshold(values: dict[str, float], *, pct: float = DEFAULT_PCT) -> float:
    """ビン代表値分布の HIGH 判定閾値（検証・デバッグ用）。空なら 0.0。"""
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(list(values.values()), dtype="float64"), float(pct)))
