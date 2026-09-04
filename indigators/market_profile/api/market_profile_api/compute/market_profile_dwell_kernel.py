"""market_profile_dwell_kernel — 実ティック滞在（dwell）の純数学コア（ISSUE-133 SRP）。

ISSUE-133（SRP）: :mod:`market_profile_dwell` に同居していた「統計コア」アクター（セッション認識滞在秒の
積分・固定グリッド日別ロールアップ）を、キャッシュ協調／運用バッチ／tick I/O アクターから分離した。
本モジュールは I/O・キャッシュ・serving オーケストレーションを持たない純関数と固定グリッド定数のみを持つ
（numpy とセッション認識カーネル :mod:`session_activity` のみに依存）。

``market_profile_dwell`` は本モジュールのシンボル（``GRID_W`` / ``session_dwell`` / ``_rollup_ticks``）
を再エクスポートし、既存の呼出面・数値を完全に温存する。

ISSUE-479 M-4: 滞在秒の積分は外部（時間加重平均を測る検証スクリプト）が同じ式を必要とする。
式を写させないため ``session_dwell`` を公開名にした。旧名 ``_session_dwell`` は**同一オブジェクト**
のまま残るので、既存参照は 1 箇所も変わらない。
"""
from __future__ import annotations

import numpy as np

# セッション認識（活発/休場地図）の純カーネル。跨ぎギャップの活発秒積分はこの唯一の規則源へ委譲する。
from market_profile_api.compute import session_activity as _session_activity
# ISSUE-178: 層間 DTO（不変・PORTING_GUIDE §2）。ロールアップは生 dict でなく frozen dataclass で返す。
from market_profile_api.compute.rollup_dto import DayRollup

GRID_W = 10.0         # 固定価格グリッド幅(pt)。日別集計→窓合算→表示 bin へ再集計する中間解像度。


def session_dwell(secs: np.ndarray, table: np.ndarray) -> np.ndarray:
    """各隣接ティック間ギャップの「活発秒」を返す（``len = len(secs)-1``）。

    同一時内は活発なら ``gap``/休場なら 0。時境界を跨ぐギャップのみ
    :func:`session_activity.active_seconds_cross` で厳密に積分する。dwell[i] はギャップ始端のティック
    （価格 mids[i]）に帰属する。
    """
    s = np.asarray(secs, dtype=np.int64)
    if s.size < 2:
        return np.zeros(max(s.size - 1, 0), dtype=float)
    start = s[:-1]
    end = s[1:]
    gap = (end - start).astype(float)
    wd = ((start // 86400) + 3) % 7
    hod = (start % 86400) // 3600
    act_start = table[wd, hod]
    same_hour = (start // 3600) == (end // 3600)
    # 同一時内: 活発なら gap、休場なら 0。
    dwell = np.where(same_hour & act_start, gap, 0.0)
    # 時境界を跨ぐギャップのみ厳密に積分（件数は僅少）。
    for i in np.where(~same_hour)[0]:
        dwell[i] = _session_activity.active_seconds_cross(int(start[i]), int(end[i]), table)
    return dwell


#: 旧 private 名（**同一オブジェクト**）。既存参照を 1 箇所も変えないために温存する。
_session_dwell = session_dwell


def _rollup_ticks(secs: np.ndarray, mids: np.ndarray, table: np.ndarray) -> "DayRollup | None":
    """ティック配列を固定グリッド :class:`DayRollup`（k=floor(mid/GRID_W)）へ集約する。空なら None。

    dwell[]: セッション認識の実ティック滞在秒（休場帯は 0）。metric='dwell'（既定）が使用する。
    cnt[]:   生ティック数（セッションマスク**非適用**＝休場帯もカウント）。metric='count'（src=m1）が使用する。

    ISSUE-178: 戻り値は不変 DTO（``dwell``/``cnt`` は ``writeable=False``）。プロセス内キャッシュと
    呼出元が同一配列を共有しても in-place 更新で汚染されない（数値・格子・空判定は不変）。
    """
    if len(secs) == 0:
        return None
    dwell = session_dwell(secs, table)  # len = len(secs)-1
    k = np.floor(mids / GRID_W).astype(np.int64)
    kmin = int(k.min())
    size = int(k.max()) - kmin + 1
    dwell_arr = np.zeros(size, dtype=float)
    if dwell.size:
        np.add.at(dwell_arr, k[:-1] - kmin, dwell)  # dwell[i] は始端ティック価格 k[i] に帰属。
    cnt_arr = np.zeros(size, dtype=float)
    np.add.at(cnt_arr, k - kmin, 1.0)  # 生ティック数（全ティック・セッション非依存）。
    return DayRollup(kmin=kmin, dwell=dwell_arr, cnt=cnt_arr)
