"""UC-R2 causal_compute — /compute の usecase 結線（proto do_compute 忠実）。

手順（proto do_compute と同順）:
    1. CausalComputePort.load_source(ref, tf)   （未知 ref/tf は ValueError）
    2. RevealClock.truncate(bars, untilTime)     （再生のその時点まで＝因果不変）
    3. tail(limit)                               （limit>0 のとき末尾 limit 本）
    4. 空なら [] を返す
    5. mode=='latest' なら FormingBar.apply(forming) して latest 計算、それ以外は full 計算

domain（reveal_clock / forming_bar）のみ依存。numpy/pandas を import しない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from simulator.replay_ui.domain.forming_bar import apply as apply_forming
from simulator.replay_ui.domain.reveal_clock import truncate

if TYPE_CHECKING:
    from simulator.replay_ui.usecase.replay_ports import CausalComputePort


@dataclass
class CausalComputeSeqRequest:
    """/compute mode='latest_seq' の入力（足内一括計算・ISSUE-232）。

    ``forming_seq`` は 1 本のバーの足内推移（ティックごとの暫定 OHLC）を昇順に並べたもの。
    各要素へ ``latest`` 計算を適用した結果を同順で返す。窓のロード・truncate・tail は
    **1 回だけ**行い、以降は forming の差し替えのみを繰り返す（1 ステップあたりの限界費用が
    指標計算そのものだけになる＝実測 load_source 242ms / latest 6.6ms）。
    """
    indicator: str
    variant: str
    ref: str
    timeframe: "str | None"
    limit: "int | None"
    until_time: "int | None"
    forming_seq: "list[dict]"
    params: dict


@dataclass
class CausalComputeRequest:
    """/compute の入力（proto body 準拠）。"""
    indicator: str
    variant: str
    ref: str
    timeframe: "str | None"
    limit: "int | None"
    until_time: "int | None"
    mode: "str | None"
    forming: "dict | None"
    params: dict


def causal_compute(
    *, request: CausalComputeRequest, compute_port: "CausalComputePort"
) -> "list[dict]":
    """series（plain dict の list）を返す。空窓は ``[]``。"""
    bars = compute_port.load_source(request.ref, request.timeframe)
    bars = truncate(bars, request.until_time)
    limit = request.limit
    if isinstance(limit, int) and limit > 0:
        bars = bars[-limit:]
    if len(bars) == 0:
        return []
    if request.mode == "latest":
        bars = apply_forming(bars, request.forming)
        return compute_port.compute(
            request.indicator, request.variant, "latest", bars, request.params
        )
    return compute_port.compute(
        request.indicator, request.variant, "full", bars, request.params
    )


def causal_compute_seq(
    *, request: CausalComputeSeqRequest, compute_port: "CausalComputePort"
) -> "list[list[dict]]":
    """足内推移の各時点の latest series を同順で返す（ISSUE-232）。

    ``causal_compute`` の mode='latest' を ``forming_seq`` の各要素へ繰り返し適用したものと
    **完全同値**である（同一の窓・同一の apply_forming・同一の latest 計算を通す）。差は
    「窓のロード / truncate / tail を 1 回に畳む」点だけで、値には影響しない。同値性は
    ``tests/unit/test_causal_compute_seq.py`` と実データのゲート実測で固定する。

    空 ``forming_seq`` は ``[]``（呼び出し自体を無害化）。空窓も ``[]``。
    """
    seq = request.forming_seq or []
    if not seq:
        return []
    bars = compute_port.load_source(request.ref, request.timeframe)
    bars = truncate(bars, request.until_time)
    limit = request.limit
    if isinstance(limit, int) and limit > 0:
        bars = bars[-limit:]
    if len(bars) == 0:
        return []
    # ISSUE-233: 形成中バーの適用は **末尾しか変えない**（apply は先頭側を触らない）。
    #   よって「共通の確定プレフィクス」と「時点ごとの末尾差分」に分けて渡し、計算側が窓を
    #   1 回だけ変換できるようにする。値は apply_forming(bars, forming) 全体を渡すのと同値
    #   （同値性は tests/unit/test_causal_compute_seq.py と forming_bar のテストで固定）。
    prefix = bars[:-1]
    tails = [apply_forming(bars[-1:], forming) for forming in seq]
    return compute_port.compute_latest_seq(
        request.indicator, request.variant, prefix, tails, request.params
    )
