"""計算量 1b（T-1・controller 経路）: 1 要求で同じ素材を 2 回引かない。

なぜこの経路を別に数えるのか（ISSUE-502 F-1・実測 2026-09-06）:
    `test_series_issued_once.py` は build_reach_sheet を直に叩くため、**controller が
    自分でも同じ instance を引いていた**ことを検出できなかった。実 HTTP ハンドラが通る
    経路（`ReachSheetController.handle`）に Spy を注入して数えると、3 instance の束で
    系列 6 発行（ユニーク 3）・同一時間足の `bars()` 3 回・`forming_bar()` 2 回だった。
    是正後は 3 / 1 / 1 である。

    実費用にならなかったのは具象 gateway が内部 memo を持っていたからで、上位の計算量が
    具象の実装詳細に依存していた。素材の取得は `usecase/sheet_supply.py` が唯一所有し、
    build_reach_sheet は P-1 / P-2 の口を取らない（二重発行が構造的に起こりえない）。

比較集合（§5.3.3）の口も同じ形へ寄せた（ISSUE-502 後続・残件 2・実測 2026-09-07）:
    `ElapsedComparisonGateway` は P-1 を自分で持ち、対象の足ごとに最小単位（1m）の系列を
    引いていた。束が同じ instance の 1m を含むとき——第 2 表は同じオシレータを 8 足ぶん
    並べるので**常態**である——同じキーが供給面とこの口の両方から発行されていた。
    Spy・合成素材での実測: 束 (1m, 5m, 15m) で 4 発行 / ユニーク 3、8 足束で 9 / 8。
    是正後はそれぞれ 3 / 3、8 / 8 である（応答は byte 等価を実測で確認済み）。

    この浪費も**出力は正しいまま**なので状態検証では原理的に落ちない。かつ既存の
    `test_no_key_is_issued_twice_on_the_controller_path` は積み上がらない量
    （moving_averages）の束しか通しておらず、比較集合の経路に一度も入らないため
    検出できなかった。以下で積み上がる量の束を別に通す。

固定するのは**無駄の不在**であって回数ではない。オーダーの表明は束の大きさ 2 点で行う。
"""
from __future__ import annotations

from dashboard_ui.adapter.controller.reach_sheet_controller import ReachSheetController
from dashboard_ui.adapter.gateway.elapsed_comparison_gateway import (
    ElapsedComparisonGateway,
)
from dashboard_ui.tests.complexity.conftest import (
    BarSpy,
    ForwardSpy,
    Registry,
    Roles,
    SeriesSpy,
    bars,
    ma_instance,
    points,
)
from dashboard_ui.usecase.sheet_models import OscillatorSpec, SheetInstance
from dashboard_ui.usecase.sheet_ports import SeriesSupplyUnavailable

REF = "jp225_tick"


def _material(unique_count: int) -> "tuple[SeriesSpy, list[SheetInstance]]":
    instances = [ma_instance(index) for index in range(1, unique_count + 1)]
    spy = SeriesSpy()
    for index, instance in enumerate(instances):
        spy.add(instance, {"MA": points([100.0 + index] * 6)})
    return spy, instances


def _controller(spy: SeriesSpy, bar_spy: BarSpy) -> ReachSheetController:
    return ReachSheetController(
        series_port=spy,
        bar_port=bar_spy,
        roles=Roles(),
        registry=Registry(set()),
        forward_port=ForwardSpy(),
        elapsed_gateway=ElapsedComparisonGateway(),
        is_intrabar_capable=lambda indicator_id, variant, params: True,
    )


def _body(instances, *, repeat: int = 1, known_state: "str | None" = None) -> dict:
    request: dict = {
        "dataset_ref": REF,
        "chart_timeframe": "1m",
        "mode": "full",
        "instances": [
            {"indicator_id": instance.indicator_id, "variant": instance.variant,
             "params": dict(instance.params), "timeframe": instance.timeframe}
            for instance in instances * repeat
        ],
    }
    if known_state is not None:
        request["known_state"] = known_state
    return request


def _used_keys(response: dict) -> "set[tuple]":
    """応答が実際に運んだ instance キー（行・セル・縮退のすべて）。"""
    used: "set[tuple]" = set()
    for row in response["rows"]:
        if row["instance_key"] is not None:
            used.add(tuple(row["instance_key"]))
    for cell in response["cells"]:
        if cell["instance_key"] is not None:
            used.add(tuple(cell["instance_key"]))
    for entry in response["degradations"]:
        used.add(tuple(entry["instance_key"]))
    return used


def test_no_key_is_issued_twice_on_the_controller_path() -> None:
    spy, instances = _material(3)
    bar_spy = BarSpy({"1m": bars([100.0] * 6)})

    response = _controller(spy, bar_spy).handle(_body(instances, repeat=3))

    assert response["ok"] is True
    assert len(spy.issued) == len(set(spy.issued))
    assert bar_spy.requested == ["1m"]
    assert bar_spy.formed == ["1m"]


def test_every_issued_key_is_carried_by_the_response() -> None:
    """発行 − 使用 = 0。使われない発行が 1 本でもあれば無駄である。"""
    spy, instances = _material(5)
    bar_spy = BarSpy({"1m": bars([100.0] * 6)})

    response = _controller(spy, bar_spy).handle(_body(instances))

    assert set(spy.issued) - _used_keys(response) == set()


def test_the_issue_count_tracks_unique_instances_and_nothing_else() -> None:
    """オーダーの表明（2 点固定）: 束の重複度を変えても発行はユニーク数のまま。"""
    counts = {}
    for unique_count in (5, 11):
        spy, instances = _material(unique_count)
        bar_spy = BarSpy({"1m": bars([100.0] * 6)})
        _controller(spy, bar_spy).handle(_body(instances, repeat=4))
        counts[unique_count] = len(spy.issued)

    assert counts[5] == 5
    assert counts[11] == 11


def test_material_requests_do_not_grow_with_the_bundle() -> None:
    """オーダーの表明（2 点固定）: 同じ足の instance が増えても素材の取得は増えない。"""
    requested = {}
    for unique_count in (5, 11):
        spy, instances = _material(unique_count)
        bar_spy = BarSpy({"1m": bars([100.0] * 6)})
        _controller(spy, bar_spy).handle(_body(instances))
        requested[unique_count] = (len(bar_spy.requested), len(bar_spy.formed))

    assert requested[5] == requested[11]


def test_no_bars_are_fetched_for_a_timeframe_no_row_can_use() -> None:
    """発行 − 使用 = 0（P-2 側）: 系列を供給できない足の素材は誰も読まない。

    供給不能な instance は縮退になるだけで行にもセルにもならない。その足を引くのは
    「作ってから捨てる」であり、出力は正しいままなので状態検証では落ちない。
    """
    class Partial(SeriesSpy):
        def full_series(self, *, indicator_id, variant, params, dataset_ref, timeframe):
            if timeframe == "1h":
                raise SeriesSupplyUnavailable("束縛がありません")
            return super().full_series(
                indicator_id=indicator_id, variant=variant, params=params,
                dataset_ref=dataset_ref, timeframe=timeframe,
            )

    spy = Partial()
    instances = [ma_instance(1), ma_instance(2, "1h")]
    spy.add(instances[0], {"MA": points([100.0] * 6)})
    bar_spy = BarSpy({"1m": bars([100.0] * 6), "1h": bars([100.0] * 6)})

    response = _controller(spy, bar_spy).handle(_body(instances))

    assert [entry["instance_key"][3] for entry in response["degradations"]] == ["1h"]
    assert bar_spy.requested == ["1m"]
    assert bar_spy.formed == ["1m"]


# ------------------------------------------- 比較集合（§5.3.3）の最小単位の系列
#: 積み上がる量（比較集合を要する側）。最小単位は 1m。
CUMULATIVE = OscillatorSpec(
    value_series="tickvol", band_high_series="tickvol_q90",
    q_high=0.9, window_n=500, k_events=50, cumulative=True,
)


def _tickvol(timeframe: str) -> SheetInstance:
    return SheetInstance("tickvol", "default", {}, timeframe, intrabar_capable=True)


def _cumulative_controller(spy: SeriesSpy, bar_spy: BarSpy) -> ReachSheetController:
    return ReachSheetController(
        series_port=spy,
        bar_port=bar_spy,
        roles=Roles({"tickvol": CUMULATIVE}),
        registry=Registry(set()),
        forward_port=ForwardSpy(),
        elapsed_gateway=ElapsedComparisonGateway(),
        is_intrabar_capable=lambda indicator_id, variant, params: True,
    )


def _cumulative_run(timeframes) -> "tuple[SeriesSpy, dict]":
    """積み上がる量の束を 1 要求ぶん通す（素材は 1m の点まで揃える）。"""
    spy = SeriesSpy()
    series = {"tickvol": points([float(index % 17 + 1) for index in range(400)]),
              "tickvol_q90": points([90.0] * 400)}
    for timeframe in {*timeframes, "1m"}:
        spy.add(_tickvol(timeframe), series)
    bar_spy = BarSpy({tf: bars([100.0] * 400) for tf in {*timeframes, "1m"}})

    response = _cumulative_controller(spy, bar_spy).handle(
        _body([_tickvol(tf) for tf in timeframes])
    )
    assert response["ok"] is True
    return spy, response


def test_the_comparison_set_does_not_reissue_the_sub_series_the_bundle_carries() -> None:
    """束が最小単位（1m）の同じ instance を持つとき、その系列を 2 回発行しない。

    第 2 表は同じオシレータを 8 足ぶん並べるので、これは例外ではなく**常態**である。
    是正前の実測: 束 (1m, 5m, 15m) で 4 発行 / ユニーク 3。
    """
    spy, _ = _cumulative_run(("1m", "5m", "15m"))

    assert len(spy.issued) == len(set(spy.issued))


def test_every_issued_key_is_carried_by_the_response_or_feeds_a_comparison() -> None:
    """発行 − 使用 = 0。比較集合の最小単位も「使った計算」に数える。

    束に 1m が無くても比較集合はその 1m 系列を読む（親足の経過を測る唯一の素材である）。
    したがって「応答に現れないから無駄」ではない——**口が宣言した需要**（`sub_instances`）
    も使い道に数える。宣言と読み取りが同じ絞りから出るので、引いたのに読まない／
    読むのに引いていない、のどちらも成立しない。

    集合の差ではなく**件数**で表明する: 差の形は同じキーの重複発行を見落とす
    （集合に畳まれるため）。「発行した数 = 実際に使ったキーの数」なら、使わない発行も
    二重発行も同じ 1 本の式が落とす。

    束は最小単位（1m）を**含む**ものを通す。含まない束では 1m の発行は初めから 1 本しか
    なく、是正前の形でもこの式は成立してしまう（検定が空振りする）。
    """
    timeframes = ("1m", "5m", "15m")
    spy, response = _cumulative_run(timeframes)
    gateway = ElapsedComparisonGateway()
    demanded = {
        instance.key
        for instance in gateway.sub_instances(
            [(_tickvol(tf), CUMULATIVE) for tf in timeframes]
        )
    }
    consumable = _used_keys(response) | demanded

    assert set(spy.issued) - consumable == set()
    assert len(spy.issued) == len(set(spy.issued) & consumable)


def test_more_parent_timeframes_do_not_issue_more_sub_series() -> None:
    """オーダーの表明（2 点固定）: 親足を増やしても最小単位の発行は 1 本のまま。

    最小単位の系列は (指標, variant, params) ごとに 1 本で足りる（親足に依らない）。
    束が 1m を含む形で測るのは上と同じ理由である——含まない束では是正前の形でも 1 本に
    見え、親足を増やしても増えないため、2 点を取っても何も落ちない。
    """
    sub_issues = {}
    for timeframes in (("1m", "5m", "15m"), ("1m", "5m", "15m", "1h", "4h", "1D", "1W")):
        spy, _ = _cumulative_run(timeframes)
        sub_issues[len(timeframes)] = [key for key in spy.issued if key[3] == "1m"]

    assert len(sub_issues[3]) == len(sub_issues[7]) == 1


def test_an_unchanged_answer_issues_no_material_beyond_the_chart_bars() -> None:
    """省リソース段階 2: 素材が動いていない要求では系列も束の足も引かない。

    素材を一括で先に引く形にすると、`unchanged` で返す要求でも束の全時間足を読むことに
    なり段階 2 が消える（出力は正しいままなので状態検証では落ちない）。
    """
    spy, instances = _material(3)
    instances.append(SheetInstance("moving_averages", "default", {"length": 99}, "1h",
                                   intrabar_capable=True))
    spy.add(instances[-1], {"MA": points([100.0] * 6)})
    bar_spy = BarSpy({"1m": bars([100.0] * 6), "1h": bars([100.0] * 6)})
    controller = _controller(spy, bar_spy)

    first = controller.handle(_body(instances))
    issued_before = len(spy.issued)
    requested_before = len(bar_spy.requested)

    second = controller.handle(_body(instances, known_state=first["state"]))

    assert second == {"ok": True, "unchanged": True, "state": first["state"]}
    assert len(spy.issued) == issued_before
    assert bar_spy.requested[requested_before:] == ["1m"]
