"""指標供給・因果性検定の Port フェイク（検定用の差替実装・Phase 3 F-5）。

usecase は Port にのみ依存する（DIP）。ここでは indicator_ui / pandas / FS という偶有的
技術を持たないフェイクを与え、**usecase の規則だけ**を検定対象にする。

置き場所は既存の `_fake_ports.py` と同じ規約（単体・結合の双方から import する）。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.usecase.indicator_models import (
    CausalityLedgerUnavailableError,
    IndicatorSpec,
    LedgerSnapshot,
    SeriesBundle,
    SeriesPoint,
)
from simulator.sim_ui.usecase.indicator_ports import (
    CausalSeriesProbePort,
    IndicatorCatalogSourcePort,
    IndicatorCausalityLedgerPort,
)


class FakeCausalSeriesProbe(CausalSeriesProbePort):
    """案 ii（`series_full`）と案 i（`series_upto`）の応答を表で与えるフェイク。

    ``full``: ``{series_name: [(time, value), ...]}``   案 ii の全点（束）。
        ``until_time`` 以下の点だけを返す（prefix 関係を実物と同じく保つ）。
    ``upto``: ``{series_name: {until_time: value}}``     案 i の末尾点。
        **表に無い時刻は「点そのものが無い」**（warmup 相当）。値 ``None`` は
        「点はあるが値が未定義」（両者は突合規則で扱いが違う）。``(time, value)`` の
        タプルは**その時刻の点**を返す（時間軸不一致を作るための注入口）。
    ``upto_names``: 案 i が返す系列名（系列集合の食い違いを作るための注入口・既定は
        ``full`` と同じ）。
    ``appear_at``: この時刻より前の窓では案 i が**系列そのものを返さない**（実物の
        warmup 挙動: 窓が短いうちは compute が pane を 1 つも返さない）。
    ``excluded``: 供給対象 kind でなかった系列名。
    """

    def __init__(
        self,
        *,
        full: "dict[str, list[tuple[int, float | None]]] | None" = None,
        upto: "dict[str, dict[int, Any]] | None" = None,
        bars: "list[int] | None" = None,
        upto_names: "list[str] | None" = None,
        appear_at: "int | None" = None,
        excluded: "list[str] | None" = None,
    ) -> None:
        self.full = full or {}
        self.upto = upto or {}
        self._bars = list(bars or [])
        self._upto_names = upto_names
        self._appear_at = appear_at
        self._excluded = list(excluded or [])
        # 呼ばれ方の記録（因果順守・呼び出し回数の実証に使う）。
        self.upto_calls: "list[int]" = []
        self.full_calls: "list[int | None]" = []

    def series_full(
        self,
        spec: IndicatorSpec,
        *,
        ref: str,
        timeframe: "str | None",
        until_time: "int | None",
    ) -> SeriesBundle:
        self.full_calls.append(until_time)
        return SeriesBundle(
            {
                name: [
                    SeriesPoint(time=t, value=v)
                    for t, v in points
                    if until_time is None or t <= until_time
                ]
                for name, points in self.full.items()
            },
            excluded=self._excluded,
        )

    def series_upto(
        self,
        spec: IndicatorSpec,
        *,
        ref: str,
        timeframe: "str | None",
        until_time: int,
    ) -> "dict[str, SeriesPoint | None]":
        self.upto_calls.append(until_time)
        if self._appear_at is not None and until_time < self._appear_at:
            return {}   # 窓が短く、まだ系列そのものが出ない
        names = self._upto_names if self._upto_names is not None else list(self.full)
        return {name: self._tail(name, until_time) for name in names}

    def _tail(self, name: str, until_time: int) -> "SeriesPoint | None":
        table = self.upto.get(name, {})
        if until_time not in table:
            return None
        value = table[until_time]
        if isinstance(value, tuple):
            return SeriesPoint(time=value[0], value=value[1])
        return SeriesPoint(time=until_time, value=value)

    def bar_times(
        self, *, ref: str, timeframe: "str | None", count: int
    ) -> "list[int]":
        return self._bars[:count] if count else list(self._bars)


class FakeCausalityLedger(IndicatorCausalityLedgerPort):
    """メモリ上の台帳。不在は本物と同じ fail-closed 例外にする。"""

    def __init__(self, snapshot: "LedgerSnapshot | None" = None) -> None:
        self.snapshot = snapshot
        self.writes: "list[LedgerSnapshot]" = []

    def read(self) -> LedgerSnapshot:
        if self.snapshot is None:
            raise CausalityLedgerUnavailableError("台帳がありません（フェイク）")
        return self.snapshot

    def write(self, snapshot: LedgerSnapshot) -> None:
        self.writes.append(snapshot)
        self.snapshot = snapshot


class FakeCatalogSource(IndicatorCatalogSourcePort):
    """検定対象母集合のフェイク。"""

    def __init__(self, specs: "list[IndicatorSpec] | None" = None) -> None:
        self._specs = list(specs or [])

    def specs(self) -> "list[IndicatorSpec]":
        return list(self._specs)
