"""byte-parity 用の決定論的合成世界（ISSUE-089）。

実 jp225_tick は 1m 原子ストアがローリング保持（窓の左端＝最古原子が壁時計と共に前進）のため、
固定クエリでも応答が時間とともに変わり byte 固定できない（golden 再赤化の最終真因）。
本モジュールは jp225_tick 向けの candles/ticks を**固定式の合成データ**へ差し替える注入器で、
byte-parity テストと再生成ツール（tools/regen_mp_byte_parity_golden.py）が同一の世界を共有する。
sample 系ケースは静的データセットのため実物のまま。

固定クエリ用エポック（golden の q に埋まる値）:
  PT_TO / PT_FROM / PT_SINCE / PT_NOW
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
# ISSUE-183 item5: 永続化設定（cache root / 形式版数）の単一情報源は gateway 側 cache_settings。
from market_profile_api.gateway import cache_settings as _mp_cache_settings

DAY0 = 1704067200  # 2024-01-01 00:00 UTC（月曜）。
SPAN_DAYS = 5
T0 = DAY0
T1 = DAY0 + SPAN_DAYS * 86400

PT_TO = DAY0 + 3 * 86400 + 4 * 3600       # 4日目 04:00 UTC（as-seen-at-t カーソル）。
PT_FROM = DAY0 + 1 * 86400                # 2日目頭（ローリング窓下限）。
PT_NOW = PT_TO
PT_SINCE = PT_NOW - 3600                  # forming 増分カーソル。


def _synth_ticks() -> "tuple[np.ndarray, np.ndarray]":
    """30 秒間隔・決定論の合成ティック（[T0, T1)・約 14,400 本）。"""
    secs = np.arange(T0, T1, 30, dtype=np.int64)
    i = np.arange(secs.size, dtype=np.float64)
    mids = 60000.0 + 300.0 * np.sin(i / 97.0) + (i % 13) * 0.5 + 40.0 * np.sin(i / 11.0)
    return secs, np.round(mids, 4)


_SECS, _MIDS = _synth_ticks()


def _synth_candles_1m() -> "list[dict]":
    """合成ティックから 1 分足原子を組む（time=分頭・OHLC/volume）。"""
    out = []
    minute = (_SECS // 60) * 60
    uniq, first = np.unique(minute, return_index=True)
    for j, t in enumerate(uniq):
        hi = first[j + 1] if j + 1 < len(first) else len(_SECS)
        seg = _MIDS[first[j]:hi]
        out.append({
            "time": int(t), "open": float(seg[0]), "high": float(seg.max()),
            "low": float(seg.min()), "close": float(seg[-1]), "volume": int(len(seg)),
        })
    return out


_CANDLES_1M = _synth_candles_1m()


def load_window_ticks(symbol, start, end):
    s, e = int(start), int(end)
    m = (_SECS >= s) & (_SECS < e)
    return _SECS[m], _MIDS[m]


def apply(setattr_fn=None) -> None:
    """合成世界を注入する（jp225_tick のみ差し替え・sample 等は実物へ委譲）。

    setattr_fn: pytest の monkeypatch.setattr（テスト＝自動復元）。None は直接 setattr
    （regen ツール＝プロセス終了まで有効）。dwell のディスクキャッシュは一時ディレクトリへ
    隔離する（実キャッシュ非汚染・値は純関数ゆえキャッシュ有無に依らず同一）。
    """
    import market_profile_api.compute.market_profile_dwell as mpd
    from marketdata import dataset as md_dataset
    import market_profile_api.controller.market_profile_controller as mpc

    def _sa(obj, name, value):
        if setattr_fn is not None:
            setattr_fn(obj, name, value)
        else:
            setattr(obj, name, value)

    real_load_candles = md_dataset.load_candles

    def load_candles(ref, timeframe=None, limit=None):
        if ref != "jp225_tick":
            return real_load_candles(ref, timeframe, limit)
        rows = _CANDLES_1M  # 原子（1m）のみ使用（parity ケースは timeframe 省略）。
        if limit is not None:
            rows = rows[-int(limit):]
        return [dict(r) for r in rows]

    _sa(mpd, "_load_window_ticks", load_window_ticks)
    _sa(mpd, "_day_source_signature", lambda symbol, day_start: "synthetic")
    _sa(_mp_cache_settings, "DWELL_CACHE_ROOT", Path(tempfile.mkdtemp(prefix="mp_parity_cache_")))
    # controller は `from marketdata import dataset` 済み＝dataset モジュール属性を差し替える。
    _sa(md_dataset, "load_candles", load_candles)
    mpd._reset_caches()
