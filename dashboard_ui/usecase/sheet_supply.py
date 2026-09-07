"""1 要求ぶんの素材（P-1 / P-2 を **要求ごとに 1 回だけ** 引いた結果）。

なぜ「口（Port）」ではなく「素材」を配るのか（ISSUE-502 F-1）:
    以前は controller（価格投影）と usecase（シート組み立て）が **同じ instance の
    `full_series` をそれぞれ発行**していた。実測 2026-09-06（Spy 注入・3 instance の束）:
    系列 6 発行 / ユニーク 3、同一時間足の `bars()` 3 回、`forming_bar()` 2 回。
    出力は正しいままなので状態検証では原理的に落ちない（ISSUE-450 / ISSUE-257 と同型）。

    二重発行が実費用にならなかったのは、具象 gateway
    （`adapter/gateway/indicator_ui_compute_gateway.py`）が内部に memo を持っていたからで
    ある。つまり上位（controller / usecase）の計算量が **具象の実装詳細に依存**していた
    ——口の差し替えで無言の浪費が復活する形である。

    ここで素材を先に確定させ、消費者へは **値として配る**。消費者は口を持たないので、
    二重発行は memo で消されるのではなく **構造的に起こしえない**（build_reach_sheet は
    `series_port` / `bar_port` を引数に取らない）。gateway の memo は要求をまたぐ共有の
    ための防御として残る（ここが担うのは 1 要求の中の畳み込みである）。

段の分離（省リソース段階 2 の維持）:
    状態トークンの材料は **表示足の足だけ**である。したがって :meth:`BarSupply.load` は
    表示足だけを引き、instance の足は :meth:`BarSupply.extended` で後から足す。
    一括で引くと `unchanged` で返す要求でも束の全時間足の素材を読み込むことになり、
    段階 2（依頼者承認 2026-08-30）が消える。系列（P-1）も同じ理由でトークン判定の
    後に引く。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from dashboard_ui.domain.bar import Bar
from dashboard_ui.usecase.sheet_models import ReachSheetRequest, SheetInstance
from dashboard_ui.usecase.sheet_ports import SeriesSupplyUnavailable


@dataclass(frozen=True)
class BarSupply:
    """P-2 を **時間足ごとに 1 回だけ** 引いた結果（足と形成中足）。

    形成中足は表示足の末尾 time（`now_unix`）で決まる 1 対 1 の派生であり、足を引いた
    時間足ぶんだけ持つ。件数は束の instance 数ではなく **時間足数**で上から抑えられる。
    """

    by_timeframe: "Mapping[str, tuple[Bar, ...]]"
    forming_by_timeframe: "Mapping[str, Bar | None]"

    @classmethod
    def load(cls, request: ReachSheetRequest, *, bar_port) -> "BarSupply":
        """表示足だけを引く（状態トークンの材料）。"""
        return cls._of(request, (request.chart_timeframe,), bar_port=bar_port)

    def extended(
        self,
        request: ReachSheetRequest,
        instances: "Sequence[SheetInstance]",
        *,
        bar_port,
    ) -> "BarSupply":
        """束の時間足を足した素材を返す（既に引いた足は **引き直さない**）。

        渡すのは**呼び出し側が実際に読む instance** である。系列を供給できない instance は
        行にもセルにもならない（縮退だけになる）ので、その足を引くのは「作ってから捨てる」
        に当たる——出力は正しいままなので状態検証では落ちない。
        """
        wanted = [
            instance.timeframe
            for instance in instances
            if instance.timeframe not in self.by_timeframe
        ]
        if not wanted:
            return self
        added = self._of(request, wanted, bar_port=bar_port,
                         now_unix=self._now_unix(request))
        return BarSupply(
            by_timeframe={**self.by_timeframe, **added.by_timeframe},
            forming_by_timeframe={
                **self.forming_by_timeframe, **added.forming_by_timeframe
            },
        )

    def of(self, timeframe: str) -> "tuple[Bar, ...]":
        """その足の全件（未取得・供給なしは空）。"""
        return self.by_timeframe.get(timeframe) or ()

    def forming(self, timeframe: str) -> "Bar | None":
        """その足の形成中足（無ければ None）。"""
        return self.forming_by_timeframe.get(timeframe)

    # ------------------------------------------------------------------ 内部
    @classmethod
    def _of(
        cls,
        request: ReachSheetRequest,
        timeframes: "Sequence[str]",
        *,
        bar_port,
        now_unix: "int | None" = None,
    ) -> "BarSupply":
        ordered: "list[str]" = []
        for timeframe in timeframes:
            if timeframe not in ordered:
                ordered.append(timeframe)
        bars = {
            timeframe: tuple(
                bar_port.bars(dataset_ref=request.dataset_ref, timeframe=timeframe)
            )
            for timeframe in ordered
        }
        moment = now_unix
        if moment is None:
            chart = bars.get(request.chart_timeframe) or ()
            moment = int(chart[-1].time) if chart else None
        forming: "dict[str, Bar | None]" = {}
        if moment is not None:
            forming = {
                timeframe: bar_port.forming_bar(
                    dataset_ref=request.dataset_ref, timeframe=timeframe,
                    now_unix=int(moment),
                )
                for timeframe in ordered
            }
        return cls(by_timeframe=bars, forming_by_timeframe=forming)

    def _now_unix(self, request: ReachSheetRequest) -> "int | None":
        chart = self.of(request.chart_timeframe)
        return int(chart[-1].time) if chart else None


@dataclass(frozen=True)
class SeriesSupply:
    """P-1 を **instance ごとに 1 回だけ** 引いた結果（系列と、供給不能の理由）。

    T-1（§7）の畳み込みはこの面が持つ。`SeriesSupplyUnavailable` は当該 instance に固有の
    構造的除外（§5.5.1）なので、ここでは握り潰さず **理由を保つ**——縮退の記録は
    build_reach_sheet が一元的に行う（二重記録を作らない）。
    """

    by_key: "Mapping[tuple, Mapping[str, tuple[tuple[int, float], ...]]]"
    unavailable: "Mapping[tuple, str]"

    @classmethod
    def load(
        cls,
        request: ReachSheetRequest,
        instances: "Sequence[SheetInstance]",
        *,
        series_port,
    ) -> "SeriesSupply":
        """束の各 instance の全件系列を引く（同一キーの発行は 1 回以下）。"""
        series: "dict[tuple, Mapping[str, tuple[tuple[int, float], ...]]]" = {}
        unavailable: "dict[tuple, str]" = {}
        for instance in instances:
            key = instance.key
            if key in series or key in unavailable:
                continue
            try:
                series[key] = dict(
                    series_port.full_series(
                        indicator_id=instance.indicator_id,
                        variant=instance.variant,
                        params=instance.params,
                        dataset_ref=request.dataset_ref,
                        timeframe=instance.timeframe,
                    )
                )
            except SeriesSupplyUnavailable as error:
                unavailable[key] = str(error)
        return cls(by_key=series, unavailable=unavailable)

    def of(self, key: tuple) -> "Mapping[str, tuple[tuple[int, float], ...]]":
        """その instance の系列（供給不能・未取得は空）。"""
        return self.by_key.get(key) or {}

    def reason_of(self, key: tuple) -> "str | None":
        """供給不能の理由（供給できていれば None）。"""
        return self.unavailable.get(key)

    def available(
        self, instances: "Sequence[SheetInstance]"
    ) -> "list[SheetInstance]":
        """系列が供給できた instance だけ（順序は与えられた順のまま）。"""
        return [instance for instance in instances if instance.key in self.by_key]
