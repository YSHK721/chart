"""1 要求ぶんの素材（P-1 / P-2）の取得規律を固定する（ISSUE-502 F-1）。

固定する核心:
  - 同一キーの系列発行は **1 回以下**（束に重複が居ても、呼び出しが 2 回でも増えない）。
  - 供給不能（`SeriesSupplyUnavailable`）は握り潰さず **理由として保つ**。例外はここから
    先へ漏らさない（縮退の記録は build_reach_sheet が一元的に持つ）。
  - 足は**表示足だけ**を先に引ける（状態トークンの材料）。束の足を足すときに、
    既に引いた足は引き直さない。
"""
from __future__ import annotations

from dashboard_ui.domain.bar import Bar
from dashboard_ui.usecase.sheet_models import ReachSheetRequest, SheetInstance
from dashboard_ui.usecase.sheet_ports import SeriesSupplyUnavailable
from dashboard_ui.usecase.sheet_supply import BarSupply, SeriesSupply

_NOW = 1_700_000_000


def _bars(count: int, *, step: int = 60) -> "tuple[Bar, ...]":
    return tuple(
        Bar(time=_NOW + index * step, open=100.0, high=101.0, low=99.0, close=100.0)
        for index in range(count)
    )


def _request(*instances: SheetInstance, chart: str = "1m") -> ReachSheetRequest:
    return ReachSheetRequest(dataset_ref="jp225_tick", instances=instances,
                             chart_timeframe=chart)


class SeriesPortSpy:
    def __init__(self, unavailable: "frozenset[str]" = frozenset()) -> None:
        self.issued: "list[tuple]" = []
        self._unavailable = unavailable

    def full_series(self, *, indicator_id, variant, params, dataset_ref, timeframe):
        self.issued.append((indicator_id, variant, timeframe))
        if indicator_id in self._unavailable:
            raise SeriesSupplyUnavailable("束縛がありません")
        return {"MA": ((_NOW, 100.0),)}


class BarPortSpy:
    def __init__(self, bars_by_timeframe) -> None:
        self._bars = dict(bars_by_timeframe)
        self.requested: "list[str]" = []
        self.formed: "list[tuple[str, int]]" = []

    def bars(self, *, dataset_ref, timeframe):
        self.requested.append(timeframe)
        return self._bars.get(timeframe, ())

    def forming_bar(self, *, dataset_ref, timeframe, now_unix):
        self.formed.append((timeframe, int(now_unix)))
        supplied = self._bars.get(timeframe) or ()
        return supplied[-1] if supplied else None


class TestSeriesSupply:
    def test_a_duplicated_instance_is_issued_only_once(self) -> None:
        instance = SheetInstance("moving_averages", "default", {"length": 5}, "1m")
        port = SeriesPortSpy()

        supply = SeriesSupply.load(
            _request(), (instance, instance), series_port=port
        )

        assert len(port.issued) == 1
        assert supply.of(instance.key) == {"MA": ((_NOW, 100.0),)}

    def test_an_unavailable_instance_is_kept_as_a_reason_not_as_an_exception(
        self,
    ) -> None:
        ok = SheetInstance("moving_averages", "default", {}, "1m")
        bad = SheetInstance("cvfe", "default", {}, "1m")
        port = SeriesPortSpy(unavailable=frozenset({"cvfe"}))

        supply = SeriesSupply.load(_request(), (ok, bad), series_port=port)

        assert supply.reason_of(bad.key) == "束縛がありません"
        assert supply.reason_of(ok.key) is None
        assert supply.of(bad.key) == {}
        assert supply.available([ok, bad]) == [ok]

    def test_an_unavailable_instance_is_not_retried(self) -> None:
        """理由を保つので、同じキーの再発行は起きない（1 回以下は失敗側にも効く）。"""
        bad = SheetInstance("cvfe", "default", {}, "1m")
        port = SeriesPortSpy(unavailable=frozenset({"cvfe"}))

        SeriesSupply.load(_request(), (bad, bad, bad), series_port=port)

        assert len(port.issued) == 1


class TestBarSupply:
    def test_only_the_chart_timeframe_is_loaded_first(self) -> None:
        port = BarPortSpy({"1m": _bars(3), "1h": _bars(3)})

        supply = BarSupply.load(_request(), bar_port=port)

        assert port.requested == ["1m"]
        assert supply.of("1m") == _bars(3)
        assert supply.of("1h") == ()

    def test_the_forming_bar_is_taken_at_the_chart_tail(self) -> None:
        port = BarPortSpy({"1m": _bars(3)})

        supply = BarSupply.load(_request(), bar_port=port)

        assert port.formed == [("1m", _NOW + 120)]
        assert supply.forming("1m") == _bars(3)[-1]

    def test_extending_does_not_re_fetch_what_is_already_held(self) -> None:
        instances = (
            SheetInstance("moving_averages", "default", {"length": 5}, "1m"),
            SheetInstance("moving_averages", "default", {"length": 9}, "1m"),
            SheetInstance("moving_averages", "default", {"length": 9}, "1h"),
        )
        port = BarPortSpy({"1m": _bars(3), "1h": _bars(3)})

        supply = BarSupply.load(_request(), bar_port=port).extended(
            _request(), instances, bar_port=port
        )

        assert port.requested == ["1m", "1h"]
        assert [timeframe for timeframe, _ in port.formed] == ["1m", "1h"]
        assert supply.of("1h") == _bars(3)

    def test_the_extended_forming_bar_uses_the_chart_moment(self) -> None:
        """形成中足の時計は表示足の末尾（他の足の末尾ではない）。"""
        instance = SheetInstance("moving_averages", "default", {}, "1h")
        port = BarPortSpy({"1m": _bars(3), "1h": _bars(2, step=3600)})

        BarSupply.load(_request(), bar_port=port).extended(
            _request(), (instance,), bar_port=port
        )

        assert port.formed == [("1m", _NOW + 120), ("1h", _NOW + 120)]

    def test_an_empty_chart_supply_yields_no_forming_bar(self) -> None:
        port = BarPortSpy({})

        supply = BarSupply.load(_request(), bar_port=port)

        assert port.formed == []
        assert supply.of("1m") == () and supply.forming("1m") is None
