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


def forming_states(
    ticks: "Sequence[Sequence[float]]",
    bar_time_fn: "Callable[[int], int]",
    *,
    seed: "dict[str, Any] | None" = None,
) -> "list[FormingState]":
    """ティック列 → 各時点の形成中バー（**唯一の畳み方・全時間足で同一**）。

    Args:
        ticks: ``[[ms, mid], ...]``（時刻昇順）。
        bar_time_fn: ``(ms) -> バー time``。「この tick はどのバーに属するか」の唯一の判定材料で、
            本モジュールは時間足を一切知らない（周期秒・floor・セッション日・暦周期の分岐を持たない）。
            呼び出し側が :func:`marketdata.tf_meta.bar_time_unix` を渡す＝ローソク（ロールアップ）と
            同一のラベル規約になり、日中足も 1D も 1W/1M も**同じ経路**で畳まれる。
        seed: 直前に確定していた形成中バー（``{"time","open","high","low","close","volume"}``）。
            同一バーなら、そこへ累積する（フロントの ``/forming_bar`` シードと同規約）。
            **バーの累積はここでしか入らない**: 呼び出し側が増分 tick だけを渡し seed を省くと、
            各 poll の先頭 tick で open/high/low/volume がリセットされる（ISSUE-251 の不具合）。

    Returns:
        tick ごとの :class:`FormingState`（入力と同数・同順）。バーが変わった tick では
        その tick を open とする新しいバーになる。
    """
    out: "list[FormingState]" = []
    cur: "dict[str, Any] | None" = dict(seed) if seed else None
    for entry in ticks:
        ms = int(entry[0])
        mid = float(entry[1])
        p = int(bar_time_fn(ms))
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
    bar_time_fn: "Callable[[int], int]",
    *,
    seed: "dict[str, Any] | None" = None,
) -> "list[FormingState]":
    """今回の増分 ``ticks`` ぶんの形成中バーを、**周期の累積を保ったまま**返す（ISSUE-251）。

    ``/live_ticks`` は ``since`` 以降の増分しか持たないため、増分だけを畳むとバーの途中で
    open/high/low/volume がリセットされる。累積は 2 つの材料で復元する:

      seed:        バー始端から ``prior_ticks`` の直前までの形成中バー（ロールアップ方式 forming）。
      prior_ticks: seed の被覆終端（秒境界）から ``ticks`` 先頭の直前までの tick（端数の埋め）。

    seed のバーが ``ticks`` 先頭のバーと違う場合（バーをまたいだ・材料不足）は seed を捨てる
    ＝ ``ticks`` 先頭から新しいバーを起こす（誤ったバーの値を引き継がない）。

    Returns:
        ``ticks`` と同数・同順の :class:`FormingState`（``prior_ticks`` ぶんは返さない）。
    """
    if not ticks:
        return []
    if seed is not None and int(seed.get("time", -1)) != int(bar_time_fn(int(ticks[0][0]))):
        seed = None
    prior = list(prior_ticks or [])
    states = forming_states(prior + list(ticks), bar_time_fn, seed=seed)
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
    *,
    wanted: "Callable[[FormingState], bool] | None" = None,
) -> "list[dict[str, Any]]":
    """各 tick 時点 × 各 spec の末尾値を組む。

    Args:
        states: :func:`forming_states` の結果。
        specs: 適用中インスタンス。
        tail_at: ``(spec, state) -> {系列名: 値}``（計算の実体。注入＝本モジュールは
            指標も pandas も知らない）。``None`` を返した spec はその tick で省略する
            （増分器を持たない指標＝保証対象外を**黙って劣化させず明示的に落とす**）。
        wanted: ``(state) -> bool``。``False`` の tick は ``tail_at`` を**呼ばない**
            （末尾値は空）。``None`` は全 tick で計算（従来どおり）。

            なぜ述語が要るか（ISSUE-257）: 末尾値の費用は tick 数 × spec 数に比例する
            （実測 0.5〜0.86 ms/tick/インスタンス）。一方フロントは「再生地平より古い tick」を
            **1 同期ループで一気に適用**するため、その区間の末尾値は最後の 1 点しか画面に出ない。
            全 tick ぶん計算すると、応答が poll 間隔を超えた瞬間に要求が重なり、重なるほど
            遅くなる正のフィードバックへ入る（収束点が無い）。**どの tick が個別に描かれるかを
            知っているのはフロント**なので、判定は注入で受け取り本モジュールは方針を持たない。

    Returns:
        ``[{"tickMs": ms, "tails": {instanceId: {系列名: 値}}}, ...]``（states と同順）。
    """
    out: "list[dict[str, Any]]" = []
    for st in states:
        tails: "dict[str, Any]" = {}
        if wanted is None or wanted(st):
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
