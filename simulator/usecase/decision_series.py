"""判定足の系列（選んだ足で判定する run・2026-10-06・依頼者承認）。

選んだ足（Period）が 1 分足でない run は、約定と SL/TP を 1 分足（`RunBacktestRequest.bars`）で
評価し、戦略には選んだ足で判定させる。その「選んだ足」側を 1 つの値に束ねる:

    ``timeframe``  選んだ足の時間足名（marketdata の時間足名・例 "1D"）。
    ``bars``       run の区間の選んだ足（戦略が判定し、チャートが表示する足）。
    ``positions``  1 分足ごとの「この足で判定する選んだ足の番号」（判定しない足は None）。
                   長さは ``RunBacktestRequest.bars`` と同じ。
    ``owners``     1 分足ごとの「属する選んだ足の番号」（長さは 1 分足と同じ）。
    ``starts`` / ``ends``  選んだ足ごとの最初・最後の 1 分足の位置（長さは ``bars`` と同じ）。
    ``indicators`` 選んだ足へ揃えた指標（位置 k ＝ ``bars[k]``）。戦略が読むものと同じ実体。

エンジンが読むのは ``positions`` だけである。残りは同じ run の結果を選んだ足で表示・記録する
読み手（売買履歴チャート・実行トレース）のために、組み立て直さずに渡す（組み立て直すと
1 分足の全行をもう 1 度読み、同じ足を作って捨てることになる）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class DecisionSeries:
    timeframe: str
    bars: Sequence[Any]
    positions: Sequence["int | None"]
    owners: Sequence[int]
    starts: Sequence[int]
    ends: Sequence[int]
    indicators: Any

    def minute_indicators(self) -> "MinuteIndicators":
        """1 分足の番号で引ける指標の見え方（位置 i ＝ 1 分足 i の時点で最後に判定した選んだ足の値）。

        エンジンが 1 分足 i の時点で読める指標は、最後に判定した選んだ足の番号の値だけである
        （判定足でない足では ``indicators.update`` を呼ばない）。終値で判定する run で「属する
        期間の値」を記録すると、その時点では分からない値になる（再レビュー B）。判定より前の
        1 分足は値なし。
        """
        last: "list[int | None]" = []
        current: "int | None" = None
        for k in self.positions:
            if k is not None:
                current = k
            last.append(current)
        return MinuteIndicators(self.indicators, last)


class MinuteIndicators:
    """選んだ足へ揃えた指標を、1 分足の番号で引く見え方（値を写さない）。

    1 分足の番号で値を読む読み手（実行トレースは評価点の足番号で引く）へ渡す。1 分足 i の値は
    i の時点で最後に判定した選んだ足の値（判定より前は NaN）。
    """

    def __init__(self, indicators: Any, owners: Sequence[int]) -> None:
        self._indicators = indicators
        self._owners = owners

    def names(self) -> "tuple[str, ...]":
        return tuple(self._indicators.names())

    def get(self, name: str) -> "_MinuteSeries":
        return _MinuteSeries(self._indicators.get(name), self._owners)


class _MinuteSeries:
    def __init__(self, series: Any, owners: Sequence[int]) -> None:
        self.iloc = _MinuteIloc(series, owners)

    def __len__(self) -> int:
        return len(self.iloc._owners)


class _MinuteIloc:
    def __init__(self, series: Any, owners: Sequence[int]) -> None:
        self._series = series
        self._owners = owners

    def __getitem__(self, i: int) -> Any:
        k = self._owners[i]
        return float("nan") if k is None else self._series.iloc[int(k)]
