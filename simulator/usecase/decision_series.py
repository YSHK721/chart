"""判定足の系列（選んだ足で判定する run・2026-10-06・依頼者承認）。

選んだ足（Period）が 1 分足でない run は、約定と SL/TP を 1 分足（`RunBacktestRequest.bars`）で
評価し、戦略には選んだ足で判定させる。その「選んだ足」側を 1 つの値に束ねる:

    ``timeframe``  選んだ足の時間足名（marketdata の時間足名・例 "1D"）。
    ``bars``       選んだ足のバー列（戦略が判定し、チャートが表示する足）。
    ``positions``  1 分足ごとの「この足で判定する選んだ足の番号」（判定しない足は None）。
                   長さは ``RunBacktestRequest.bars`` と同じ。
    ``indicators`` 選んだ足へ揃えた指標（位置 k ＝ ``bars[k]``）。戦略が読むものと同じ実体。

エンジンが読むのは ``positions`` だけである。残りは同じ run の結果を選んだ足で表示する
読み手（売買履歴チャート）のために、組み立て直さずに渡す（組み立て直すと 1 分足の全行を
もう 1 度読み、同じ足を作って捨てることになる）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class DecisionSeries:
    timeframe: str
    bars: Sequence[Any]
    positions: Sequence["int | None"]
    indicators: Any

    def period_end_positions(self) -> "list[int]":
        """選んだ足 k ごとの「その期間の最後の 1 分足」の位置（k の昇順）。

        期間 k は位置 ``positions`` が k になる 1 分足から、次の判定足の手前まで続く。
        表示する選んだ足の本数と判定足の本数が違えば（1 分足を 1 本も持たない選んだ足が
        在れば）、その足の口座の値は存在しないので `ValueError` にする（推測で埋めない）。
        """
        starts = [i for i, k in enumerate(self.positions) if k is not None]
        if [self.positions[i] for i in starts] != list(range(len(self.bars))):
            raise ValueError(
                f"判定足 {len(starts)} 本が選んだ足 {len(self.bars)} 本と 1 対 1 に対応しません"
            )
        return [nxt - 1 for nxt in starts[1:]] + [len(self.positions) - 1]
