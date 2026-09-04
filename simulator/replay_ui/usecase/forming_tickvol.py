"""UC-R7 forming_tickvol — 形成中バーの実 tick 数（足内の tick カウント）。

①なぜ必要か（ISSUE-238）:
    リプレイの形成中バーはフロントが OHLC だけで作るため、``forming_bar.apply`` の規約
    （**forming に存在するキーのみ**更新）により **volume は確定足の完成値が残る**。結果、
    volume を読む指標（tickvol / profit_mfi 等）は足の先頭から完成後の値を表示し、足内で
    一度も動かない＝未来先取りになる。形成中バーへ「その時点までの実 tick 数」を持たせて
    真因を除去する。

②定義（ライブの参照実装と同一）:
    ライブは ``adapter.compute.forming_bar`` が窓 ``[足始端, now)`` の実 tick 数を
    ``volume = len(mids)`` として与える。リプレイの ``now`` は ``to``（ISSUE-129 で確定した
    単一時計）であり、各足内時点の ``to`` はフロントが持つ（real_ticks＝実 tick 秒／
    合成モード＝窓等分。MP tick-live が既に採用している時計と同一）。
    本 usecase は **``[win_start, to]`` の実 tick 数** を返す（``to`` 時点で到来済みの tick）。

③tick 集合の同一性（実測 2026-08-01）:
    数える対象は ``/intraday`` が返すのと同じ mid 列（domain E-4 ``mid_series``＝窓フィルタ・
    mid 算出・中央値外れ値除去）である。実測で窓 [1785528000,1785528300) の mid 列は 770 件、
    同区間の確定足 tickvol も 770 で **完全一致** する（同 [1785528300,1785528600) は 297 で一致）。
    よって足終端で形成中の値は確定値へ厳密に収束する（段差が出ない）。

④依存: domain（tick_mid_series）と :class:`IntrabarWindowPort` のみ。numpy/pandas を import しない。
"""
from __future__ import annotations

from bisect import bisect_right
from typing import TYPE_CHECKING

from simulator.replay_ui.domain.tick_mid_series import OUTLIER_THRESHOLD, mid_series

if TYPE_CHECKING:
    from simulator.replay_ui.usecase.replay_ports import IntrabarWindowPort


def forming_tick_counts(
    *,
    window_port: "IntrabarWindowPort",
    win_start: "int | None",
    win_end: "int | None",
    tos: "list[int | float | None]",
    threshold: float = OUTLIER_THRESHOLD,
) -> "list[int | None]":
    """各 ``to`` 時点までに到来した実 tick 数を同順で返す（不明は ``None``）。

    Args:
        window_port: 生ティック供給（``load_raw_ticks``）。
        win_start / win_end: 足内窓（フロントの ``intrabarWindow`` が唯一の規則源＝
            1D のセッション日境界・1W/1M の右ラベル規約をサーバへ写さない）。
        tos: 各時点のリプレイ現在時刻（UNIX 秒）。``None`` の要素は ``None`` を返す。

    Returns:
        ``tos`` と同順・同長の list。窓不正・ティック取得失敗・ティック 0 件では全要素 ``None``
        （＝呼び出し側は volume を注入せず従来どおり＝挙動を勝手に変えない）。
    """
    n = len(tos)
    if n == 0:
        return []
    if win_start is None or win_end is None:
        return [None] * n
    try:
        start, end = int(win_start), int(win_end)
    except (TypeError, ValueError):
        return [None] * n
    if not (end > start):
        return [None] * n
    try:
        raw = window_port.load_raw_ticks(start, end)
        rows = mid_series(raw, start, end, threshold=threshold)
    except Exception:  # noqa: BLE001 — ティック取得失敗は「不明」へ縮退（計算全体を落とさない）
        return [None] * n
    if not rows:
        return [None] * n

    secs = [sec for sec, _mid in rows]   # mid_series は時系列順（昇順）を保つ
    out: "list[int | None]" = []
    for to in tos:
        if to is None:
            out.append(None)
            continue
        try:
            t = int(to)
        except (TypeError, ValueError):
            out.append(None)
            continue
        # [win_start, to]（to 時点で到来済み）。ライブの半開区間 [start, now) と同じ意味で、
        #   `to` が「現在時刻」そのものである点だけが違う（リプレイ時計は点の時刻を指す）。
        out.append(bisect_right(secs, t))
    return out


def with_tick_volume(
    forming: "dict | None", count: "int | None"
) -> "dict | None":
    """形成中バー dict へ実 tick 数を ``volume`` として載せた **新しい** dict を返す。

    ``count`` が ``None``（不明）なら入力をそのまま返す（volume を作らない＝従来挙動）。
    入力は破壊しない（``forming_bar.apply`` と同じく非破壊の規律）。
    """
    if count is None or not isinstance(forming, dict):
        return forming
    out = dict(forming)
    out["volume"] = float(count)
    return out
