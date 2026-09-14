"""market_profile_forming — MP サブバー tick 逐次成長の「形成中期間の tick 列 + active table」供給（薄い adapter）。

Phase2 設計 mp_ticklive_design.md「新規 backend compute」。クライアント側 DwellAccumulator が per-tick HTTP を
行わずローカル増分できるよう、形成中期間 ``[floor(now, tf), now)`` の実ティック列と active table を供給する。

責務（読み取り専用・既存不変）:
  - forming_ticks(symbol, tf, now, since): 形成中期間の tick 列 ``[[sec, mid]...]``。
      始端 ``formingStart = floor(now, tf)`` は :func:`forming_bar.period_start_unix`（規則源・DRY）へ委譲。
      tick は :func:`market_profile_dwell._load_window_ticks`（単一注入点・read-only）を再利用して得る。
      ``since`` 指定時は ``sec > since`` の尾部のみ（クライアント増分の差分取得）。
  - get_active_table(symbol): 活発秒判定地図（7×24 int）を :func:`market_profile_dwell.get_active_table` から露出。

依存方向: 本モジュールは既存 :mod:`market_profile_dwell` / :mod:`forming_bar` を import して使うだけ（既存
データ・既存関数シグネチャは非改変）。
"""

from __future__ import annotations

from typing import Any

from marketdata import tf_meta as _forming_bar  # ISSUE-087 🔴-1: 裸 adapter 依存を排し単一情報源を参照
from market_profile_api.compute import market_profile_dwell as _mpd


def forming_ticks(symbol: str, tf: str, now: Any, since: Any = None) -> dict:
    """形成中期間 ``[floor(now, tf), now)`` の tick 列を返す。

    Returns:
        ``{formingStart, ticks:[[sec,mid]...], now}``。``formingStart = floor(now, tf)``（UNIX 秒）。
        ``ticks`` は時系列順の ``[int(sec), float(mid)]``。``since`` 指定時は ``sec > since`` の尾部のみ。
    """
    now_i = int(now)
    forming_start = _forming_bar.period_start_unix(now_i, tf)
    # 単一注入点（テストが monkeypatch する）をモジュール属性経由で呼ぶ（read-only・外れ値除去/時系列順込み）。
    secs, mids = _mpd._load_window_ticks(symbol, forming_start, now_i)
    since_i = None if since is None else int(since)
    ticks: list[list] = []
    for sec, mid in zip(secs, mids):
        sec_i = int(sec)
        if since_i is not None and sec_i <= since_i:
            continue  # 尾部フィルタ: 既取得済み（sec<=since）を除外し差分のみ返す。
        ticks.append([sec_i, float(mid)])
    return {"formingStart": int(forming_start), "ticks": ticks, "now": now_i}


def get_active_table(symbol: str) -> list[list[int]]:
    """活発秒判定地図（7 曜日×24 時・0/1）を返す（:func:`market_profile_dwell.get_active_table` へ委譲）。"""
    return _mpd.get_active_table(symbol)


def augment_forming_ticks(
    parquet_ticks: Any,
    buffer_ticks: Any,
    forming_start: int,
    now_unix: int,
    since: Any = None,
) -> list:
    """MP 形成中期間の tick 列 ``[[sec, mid]...]`` を in-memory buffer で補完する（秒成長の遅延解消）。

    ISSUE-094 🟡-8: 本純関数は MP forming payload 整形（A7）であり、indicator_ui/adapter/compute/
    forming_bar（形成中バー算出 A4）から MP compute へ移設した（forming_bar は互換のため再エクスポート）。

    parquet 由来 ``parquet_ticks``（``[[sec, mid]...]``・当日フロンティア遅延で末尾が欠ける）に、
    ``buffer_ticks``（``[(unix_ms, mid)...]``・near-real-time）のうち **parquet 被覆の最終秒より後**かつ
    窓 ``[forming_start, now_unix)`` 内の tick を **秒重複なく**追加する（parquet 優先）。``since`` 指定時は
    合成結果へ ``sec > since`` を適用（クライアント既取得分を除外＝base=0 増分）。純関数（I/O 無し）。
    buffer 空 → parquet の窓内クランプのみ＝現行挙動不変。
    """
    lo, hi = int(forming_start), int(now_unix)
    result = [[int(t[0]), float(t[1])] for t in parquet_ticks if lo <= int(t[0]) < hi]
    parquet_max = max((t[0] for t in result), default=lo - 1)
    for tk in buffer_ticks or ():
        sec = int(tk[0] // 1000)
        if lo <= sec < hi and sec > parquet_max:  # parquet 末尾より後だけ補完（二重計上なし）
            result.append([sec, float(tk[1])])
    if since is not None:
        s = int(since)
        result = [t for t in result if t[0] > s]
    result.sort(key=lambda t: t[0])
    return result
