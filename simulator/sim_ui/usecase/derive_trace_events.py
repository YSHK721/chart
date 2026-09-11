"""事象の導出（usecase 層・RUN_TRACE_BASIC_DESIGN §9.3）。

アクター（改訂の動機）: **何を「出来事」と呼ぶか**。trace は**状態列**であって事象列
ではない。「いつ halt したか」「いつ建玉が増減したか」「いつ維持率が閾値を割ったか」は
状態の**遷移**であり、列そのものには書かれていない。窓・列の解釈
（`simulator/sim_ui/usecase/query_trace.py`）とも、読み方
（`simulator/adapter/trace/parquet_trace_store.py`）とも別の理由で変わる。

**遷移であって状態ではない**（設計の要）:
    halt している 100 万点を 100 万件の「halt 事象」にしたら一覧は読めない。報告するのは
    値が**変わった瞬間**だけである。したがって先頭行には特別な扱いが要る——直前が
    存在しないからである。ここで「直前は False だった」と仮定すると、halt 後に切った窓が
    「halt していない状態から始まった」という**別の事実**に化ける。先頭行は
    「直前が無い」ものとして、真である状態だけを 1 件立てる（発明しない）。

**1 回だけ走査する**（絶対命令・§9.6）:
    種別ごとに列を走査し直すと、出力は正しいまま走査回数が種別数倍になる。状態検証では
    原理的に落ちないので、`simulator/sim_ui/tests/unit/test_derive_trace_events.py` の
    計算量テストが Test Spy で走査回数を数えて固定する。

依存規律: usecase 層である。adapter を掴まない・pandas / pyarrow を import しない
（列は素の並びで受ける）。`simulator/usecase/*.py` の pandas 禁止ゲートの対象外だが、
同じ規律に従う（`sim_ui/usecase` が pandas を掴めば Port の意味が消える）。

語彙: front の `removed_ui_vocabulary_gate.test.js` が trailing / partial の語を
`js/adapter/front/*.js` からコメント含め排除している。事象名は front がそのまま
表示語彙として受け取るので、これらの語を使わない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: 実行が停止した（`halted` が偽 → 真）。
HALT = "halt"
#: 実行が再開した（`halted` が真 → 偽）。
RESUME = "resume"
#: 保有玉数が増えた。
POSITION_OPENED = "position_opened"
#: 保有玉数が減った。
POSITION_CLOSED = "position_closed"
#: 証拠金維持率が閾値を**下抜けた**（閾値ちょうどは割れていない）。
MARGIN_FLOOR_BREACH = "margin_floor_breach"

#: 導出しうる事象種別の**宣言**（front の凡例・検定はこれを読む。列挙を写さない）。
EVENT_KINDS: "tuple[str, ...]" = (
    HALT, RESUME, POSITION_OPENED, POSITION_CLOSED, MARGIN_FLOOR_BREACH,
)

#: 導出に要る状態列の**宣言**（query_trace が読む量を決めるときに使う）。
#: ここを写すと「読む列」と「使う列」が 2 箇所で食い違い、余分な列を読むことになる。
REQUIRED_COLUMNS: "tuple[str, ...]" = (
    "time", "halted", "open_count", "margin_level",
)


@dataclass(frozen=True)
class TraceEvent:
    """1 件の事象。

    ``time``: 起きた時刻（epoch ミリ秒・`time` 列と同じ単位）。
    ``kind``: `EVENT_KINDS` のいずれか。
    ``previous``: 直前の値（先頭行は `None`＝直前が存在しない）。
    ``value``: そのときの値。
    """

    time: int
    kind: str
    previous: Any
    value: Any

    def to_dict(self) -> "dict[str, Any]":
        return {
            "time": self.time,
            "kind": self.kind,
            "previous": self.previous,
            "value": self.value,
        }


class DeriveTraceEventsInteractor:
    """状態列から事象列を導出する（1 走査）。"""

    def execute(
        self,
        columns: "Mapping[str, Any]",
        *,
        margin_level_floor: "float | None",
    ) -> "tuple[TraceEvent, ...]":
        """`REQUIRED_COLUMNS` の状態列から事象を並べる。

        事前条件: ``columns`` は `REQUIRED_COLUMNS` を持ち、各列は同じ長さの並び。
        事後条件: 時刻昇順の `TraceEvent` を返す（同一時刻の複数種別は宣言順）。
        例外: 列が欠けていれば `KeyError`（黙って 0 件にしない——「事象が無い run」と
            「列を取り落とした」は別の事実である）。

        ``margin_level_floor`` が `None` のとき維持率の割れは導出しない。閾値が無ければ
        「割れ」は定義できず、既定値を発明すれば run と無関係な事象が並ぶ（§7）。
        """
        times = columns["time"]
        # zip は各列を 1 度ずつ引く（種別ごとの再走査を作らない）。
        rows = zip(times, columns["halted"], columns["open_count"], columns["margin_level"])

        events: "list[TraceEvent]" = []
        first = True
        prev_halted: Any = None
        prev_count: Any = None
        prev_below: Any = None
        for time, halted, open_count, margin_level in rows:
            below = (
                margin_level_floor is not None and margin_level < margin_level_floor
            )
            if first:
                # 直前が無い。真である状態だけを 1 件立てる（偽の状態を「戻った」とは
                # 呼ばない——戻る前が存在しないため）。
                if halted:
                    events.append(TraceEvent(time, HALT, None, halted))
                if below:
                    events.append(
                        TraceEvent(time, MARGIN_FLOOR_BREACH, None, margin_level)
                    )
                first = False
            else:
                if halted != prev_halted:
                    events.append(
                        TraceEvent(
                            time, HALT if halted else RESUME, prev_halted, halted
                        )
                    )
                if open_count != prev_count:
                    events.append(
                        TraceEvent(
                            time,
                            POSITION_OPENED if open_count > prev_count else POSITION_CLOSED,
                            prev_count,
                            open_count,
                        )
                    )
                if below and not prev_below:
                    events.append(
                        TraceEvent(
                            time, MARGIN_FLOOR_BREACH, prev_below, margin_level
                        )
                    )
            prev_halted = halted
            prev_count = open_count
            prev_below = below
        return tuple(events)
