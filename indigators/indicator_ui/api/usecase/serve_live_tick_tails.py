"""ライブの毎ティック指標末尾値（ISSUE-250 Phase 1・純ロジック）。

目的（真因の除去）:
    ライブは tick 適用ごとに ``/compute`` へ HTTP 往復して末尾点を取り直しており、
    scheduler が in-flight 1 本へ coalesce するため「指標更新回数 == ローソク更新回数」が
    構成上成立しなかった（ISSUE-157 で確定した「1 往復 1 回」）。本モジュールは
    **ティック列に対する各時点の末尾値をサーバ側で一括算出**し、``/live_ticks`` の応答へ
    同梱できる形にする。フロントは tick 適用と同一同期ブロックで描けるため、往復が
    tick 路から消え、回数一致がリプレイと同じく構成上の保証になる。

    先読みが可能な理由: ライブは表示を 12 秒遅延させている（``live_tick_player.DELAY_MS``）。
    その 12 秒がそのままサーバの計算納期バッファになる。

形成中バーの畳み方（**単一定義**）:
    :func:`forming_states` が唯一の規則で、リプレイ ``forming_plan.formingStatesAt`` と同型:
      open  = その周期で最初に適用された tick の mid（以後固定）
      high  = 流入 tick の累積最大 / low = 累積最小
      close = その時点の tick の mid
      volume= その周期でそれまでに適用された tick 数
    ここがフロント（``live_tick_player._applyTick``）とずれると描画状態と値が食い違う
    （ISSUE-232 で実際に起きた失敗モード）。フロントは本規則の結果を受け取るだけにする。

    畳む対象は「周期の全 tick」であって「今回の poll の増分」ではない（ISSUE-251）。増分だけを
    畳むと 2 回目以降の poll で累積が消える（実測: ローソク volume=40 に対し tickvol 末尾値=2）。
    増分しか手元に無い呼び出し側は :func:`states_for_batch` を使い、seed（周期始端からの累積）と
    prior_ticks（seed の被覆終端から増分先頭までの端数）で累積を復元する。

依存方向: 標準ライブラリと注入された協調子のみ。pandas / HTTP / 具象 Store に依存しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence


@dataclass(frozen=True)
class FormingState:
    """ある tick 時点の形成中バー（``time`` は周期始端の UNIX 秒）。"""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    tick_ms: int


def period_of(ms: int, tf_sec: int) -> int:
    """tick 時刻（ms）→ 所属周期の始端 UNIX 秒（フロントの ``_periodOf`` と同一規則）。

    日中足の UTC floor 規則のみを担う。``1D`` はセッション日境界（ISSUE-078）であり本関数では
    表せないため、呼び出し側が ``period_fn`` でセッション日の規則を注入する（本モジュールは
    marketdata に依存しない）。
    """
    return (int(ms) // 1000 // int(tf_sec)) * int(tf_sec)


def forming_states(
    ticks: "Sequence[Sequence[float]]",
    tf_sec: int,
    *,
    seed: "dict[str, Any] | None" = None,
    period_fn: "Callable[[int, int], int] | None" = None,
) -> "list[FormingState]":
    """ティック列 → 各時点の形成中バー（**唯一の畳み方**）。

    Args:
        ticks: ``[[ms, mid], ...]``（時刻昇順）。
        tf_sec: 時間足の秒数。
        seed: 直前に確定していた形成中バー（``{"time","open","high","low","close","volume"}``）。
            同一周期なら、そこへ累積する（フロントの ``/forming_bar`` シードと同規約）。
            **周期の累積はここでしか入らない**: 呼び出し側が増分 tick だけを渡し seed を省くと、
            各 poll の先頭 tick で open/high/low/volume がリセットされる（ISSUE-251 の不具合）。
        period_fn: ``(ms, tf_sec) -> 周期キー``。既定は :func:`period_of`（UTC floor）。
            ``1D`` はセッション日境界（ISSUE-078）のため呼び出し側が注入する。

    Returns:
        tick ごとの :class:`FormingState`（入力と同数・同順）。周期が変わった tick では
        その tick を open とする新しいバーになる。
    """
    out: "list[FormingState]" = []
    cur: "dict[str, Any] | None" = dict(seed) if seed else None
    period = period_fn or period_of
    for entry in ticks:
        ms = int(entry[0])
        mid = float(entry[1])
        p = period(ms, tf_sec)
        if cur is None or int(cur["time"]) != p:
            cur = {"time": p, "open": mid, "high": mid, "low": mid, "close": mid, "volume": 1}
        else:
            cur = {
                "time": p,
                "open": float(cur["open"]),
                "high": max(float(cur["high"]), mid),
                "low": min(float(cur["low"]), mid),
                "close": mid,
                "volume": int(cur["volume"]) + 1,
            }
        out.append(FormingState(
            time=p, open=float(cur["open"]), high=float(cur["high"]),
            low=float(cur["low"]), close=float(cur["close"]),
            volume=int(cur["volume"]), tick_ms=ms,
        ))
    return out


def states_for_batch(
    prior_ticks: "Sequence[Sequence[float]]",
    ticks: "Sequence[Sequence[float]]",
    tf_sec: int,
    *,
    seed: "dict[str, Any] | None" = None,
    period_fn: "Callable[[int, int], int] | None" = None,
) -> "list[FormingState]":
    """今回の増分 ``ticks`` ぶんの形成中バーを、**周期の累積を保ったまま**返す（ISSUE-251）。

    ``/live_ticks`` は ``since`` 以降の増分しか持たないため、増分だけを畳むと周期の途中で
    open/high/low/volume がリセットされる。累積は 2 つの材料で復元する:

      seed:        周期始端から ``prior_ticks`` の直前までの形成中バー（ロールアップ方式 forming）。
      prior_ticks: seed の被覆終端（秒境界）から ``ticks`` 先頭の直前までの tick（端数の埋め）。

    seed の周期が ``ticks`` 先頭の周期と違う場合（周期をまたいだ・材料不足）は seed を捨てる
    ＝ ``ticks`` 先頭から新しいバーを起こす（誤った周期の値を引き継がない）。

    Returns:
        ``ticks`` と同数・同順の :class:`FormingState`（``prior_ticks`` ぶんは返さない）。
    """
    if not ticks:
        return []
    period = period_fn or period_of
    if seed is not None and int(seed.get("time", -1)) != period(int(ticks[0][0]), tf_sec):
        seed = None
    prior = list(prior_ticks or [])
    states = forming_states(prior + list(ticks), tf_sec, seed=seed, period_fn=period_fn)
    return states[len(prior):]


@dataclass(frozen=True)
class TailSpec:
    """クライアントが適用中の 1 インスタンス（末尾値を要求する単位）。"""

    instance_id: str
    indicator_id: str
    variant: str
    params: "dict[str, Any]"


def tails_for_ticks(
    states: "Iterable[FormingState]",
    specs: "Sequence[TailSpec]",
    tail_at: "Callable[[TailSpec, FormingState], dict[str, float] | None]",
) -> "list[dict[str, Any]]":
    """各 tick 時点 × 各 spec の末尾値を組む。

    Args:
        states: :func:`forming_states` の結果。
        specs: 適用中インスタンス。
        tail_at: ``(spec, state) -> {系列名: 値}``（計算の実体。注入＝本モジュールは
            指標も pandas も知らない）。``None`` を返した spec はその tick で省略する
            （増分器を持たない指標＝保証対象外を**黙って劣化させず明示的に落とす**）。

    Returns:
        ``[{"tickMs": ms, "tails": {instanceId: {系列名: 値}}}, ...]``（states と同順）。
    """
    out: "list[dict[str, Any]]" = []
    for st in states:
        tails: "dict[str, Any]" = {}
        for spec in specs:
            values = tail_at(spec, st)
            if values:
                tails[spec.instance_id] = values
        out.append({"tickMs": st.tick_ms, "tails": tails})
    return out


def parse_specs(raw: Any) -> "list[TailSpec]":
    """クライアント申告（JSON 配列）→ :class:`TailSpec` 列（不正要素は捨てる）。"""
    if not isinstance(raw, list):
        return []
    specs: "list[TailSpec]" = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        iid = item.get("instanceId")
        cid = item.get("indicatorId")
        if not isinstance(iid, str) or not isinstance(cid, str):
            continue
        params = item.get("params")
        specs.append(TailSpec(
            instance_id=iid, indicator_id=cid,
            variant=str(item.get("variant") or "default"),
            params=params if isinstance(params, dict) else {},
        ))
    return specs
