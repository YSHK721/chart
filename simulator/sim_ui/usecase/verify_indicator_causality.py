"""UC-S3 因果性検定（案 i と案 ii の全バー突合・usecase 層・Phase 3 F-5）。

基本設計書 §3.5.4 の裁定: 供給の既定は案 ii（全期間を 1 回 full 計算して配列から引く）
とし、**正しさの基準は案 i（バーごとに until_time で truncate して逐次計算）に置く**。
系列ごとに全バーの一致を実測し、一致した系列だけを sim モードで選択可能にする。
不一致は選択不可として明示する（無音で誤った値を使わない）。

判定規則:
    1. 案 i が案 ii に無い系列を返したら**比較不能**（:class:`CausalityComparisonError`）。
       同じものを比べていない状態を「不一致」と記録しない。
       逆向き（案 i に系列が現れない）は**その時刻に点が無い**として扱う。窓が短いうちは
       compute が pane を 1 つも返さないためである（2026-08-11 実測: jp225_tick/5m の
       データセット先頭 20 本では moving_averages の応答が空 list）。ここを不一致にすると
       検定は必ず先頭バーで倒れ、全系列が選択不可になる＝検定が常に空振りする。
       案 ii がその時刻に点を持つのに案 i が持たない場合は規則 2 が捕まえる。
    2. time で突合する。片側にのみ点が存在する時刻も比較不能。ただし**先頭 warmup
       prefix**（案 i がまだ 1 点も出せていない区間で、案 ii だけが点を持つバー）は
       比較対象外とし、本数を ``warmup_bars`` として記録する。
       根拠（2026-08-11 実測・jp225_tick/5m・moving_averages length=9）: 窓が 9 本未満の
       とき compute は pane を 1 つも返さないのに対し、案 ii は先頭バーから点を持つ。
       これは値のずれではなく「指標が値を出し始める最小本数」の差であり、ここを比較不能に
       すると検定は必ずデータセット先頭で倒れる。**隠さずに数えて台帳へ残す**ことで、
       供給窓の先頭 ``warmup_bars`` 本は案 i が出せない値である事実を後段へ渡す。
       案 i が 1 点でも出したあとの片側欠落は比較不能のまま（規則を緩めない）。
    3. 値は厳密一致（``tolerance`` 既定 0.0）。tolerance は測定条件として台帳に残す。
    4. ``None`` 同士（未定義点）は一致・片側だけ ``None`` は不一致。
    5. 案 i の問い合わせは各バー時刻を until_time にする（因果順守）。
    6. 比較したバーが 0 本のとき一致を主張しない（fail-closed）。
    7. 判定は**系列ごと**に独立して下す（1 系列の不一致で他系列を巻き添えにしない）。

費用の規約: 1 バーにつき案 i の計算は **1 回**（束契約）。系列ごとに呼ぶと同じ計算を
系列数ぶん重複して払う（実測: 26 組が 122 系列に多重化し 1 パス 138.5 秒）。

CLEAN_ARCH: usecase 層。Port（:class:`CausalSeriesProbePort`）にのみ依存し、
indicator_ui / pandas / FS を知らない。
"""
from __future__ import annotations

import time
from typing import Sequence

from simulator.sim_ui.usecase.indicator_models import (
    REASON_MISMATCH,
    REASON_VERIFICATION_INCOMPLETE,
    CausalityComparisonError,
    CausalityFinding,
    IndicatorSpec,
    SeriesPoint,
    SupplyCost,
)
from simulator.sim_ui.usecase.indicator_ports import CausalSeriesProbePort


def measure_supply_cost(
    *,
    spec: IndicatorSpec,
    ref: str,
    timeframe: "str | None",
    until_time: "int | None",
    probe: CausalSeriesProbePort,
) -> SupplyCost:
    """供給（案 ii の 1 回計算）の所要秒と、その結果の系列束を実測する（段 0）。

    通過条件 3（供給窓 1 万本で 1 秒以内）の証拠はこの秒数である。得られた束を捨てずに
    返すのは、測り直すと供給コストを二重に払うため。
    """
    started = time.perf_counter()
    bundle = probe.series_full(spec, ref=ref, timeframe=timeframe, until_time=until_time)
    return SupplyCost(seconds=time.perf_counter() - started, bundle=bundle)


def verify_indicator_causality(
    *,
    spec: IndicatorSpec,
    ref: str,
    timeframe: "str | None",
    bar_times: "Sequence[int]",
    probe: CausalSeriesProbePort,
    tolerance: float = 0.0,
    timeout: "float | None" = None,
    supply_seconds: "float | None" = None,
) -> "list[CausalityFinding]":
    """1 指標の**全系列**を全バーで突合し、系列ごとの結果を返す。

    ``timeout``: 検定予算（秒）。超過した時点で打ち切り、まだ一致が確定していない系列を
    :data:`REASON_VERIFICATION_INCOMPLETE` として記録する（既に不一致が確定した系列は
    :data:`REASON_MISMATCH` のまま）。「予算内に測り切れなかった」ことを「一致した」とも
    「値がずれた」とも書かない。

    比較不能（規則 1・2）は :class:`CausalityComparisonError` を送出する。呼び出し側が
    「選択不可の理由」へ翻訳するか、検定を止めるかを決める。
    """
    times = [int(t) for t in bar_times]
    if not times:
        # 規則 6: 0 本の一致を「一致」と呼ばない。系列も判らないので束を取りに行かない。
        return []

    full = probe.series_full(spec, ref=ref, timeframe=timeframe, until_time=times[-1])
    names = sorted(full)
    full_by_time = {
        name: {int(p.time): p.value for p in full[name]} for name in names
    }

    state = {name: _SeriesState() for name in names}
    started = time.perf_counter()
    incomplete: "str | None" = None
    for index, bar_time in enumerate(times):
        tails = probe.series_upto(spec, ref=ref, timeframe=timeframe, until_time=bar_time)
        extra = set(tails) - set(names)
        if extra:
            # 規則 1: 案 i にしかない系列＝同じものを比べていない。
            raise CausalityComparisonError(
                f"案 i にのみ存在する系列があります: {sorted(extra)}"
                f"（案 ii={sorted(names)}）"
            )
        for name in names:
            state[name].compare(
                bar_time=bar_time,
                # 規則 1: 案 i にその系列が現れない＝その時刻に点が無い（warmup）。
                point=tails.get(name),
                full_values=full_by_time[name],
                tolerance=tolerance,
                series_name=name,
            )
        # 予算の判定は 1 本を測り終えてから行う（測れたぶんの結果は捨てない）。
        # 最終バーまで測り終えていれば超過でも「測り切れなかった」ではない。
        remaining = len(times) - index - 1
        if remaining and timeout is not None and (time.perf_counter() - started) > timeout:
            incomplete = (
                f"検定予算 {timeout} 秒を超過しました"
                f"（{index + 1}/{len(times)} 本まで検定・残り {remaining} 本）"
            )
            break

    return [
        state[name].to_finding(
            spec=spec,
            series_name=name,
            supply_seconds=supply_seconds,
            incomplete=incomplete,
        )
        for name in names
    ]


class _SeriesState:
    """1 系列ぶんの突合状態（比較本数・warmup 本数・最大差・最初の不一致時刻）。"""

    def __init__(self) -> None:
        self.compared = 0
        self.warmup = 0
        self.started = False
        self.max_abs_diff = 0.0
        self.first_mismatch_time: "int | None" = None

    def compare(
        self,
        *,
        bar_time: int,
        point: "SeriesPoint | None",
        full_values: "dict[int, float | None]",
        tolerance: float,
        series_name: str,
    ) -> None:
        if point is not None and int(point.time) != bar_time:
            # 規則 2: 案 i の点の時刻が対象バーと違う＝時間軸が揃っていない。
            raise CausalityComparisonError(
                f"{series_name}: 案 i の点の時刻が対象バーと異なります: "
                f"bar={bar_time} point={int(point.time)}"
            )
        has_causal = point is not None
        has_full = bar_time in full_values
        if has_full and not has_causal and not self.started:
            # 規則 2: 先頭 warmup prefix（案 i がまだ値を出せる本数に達していない）。
            #   隠さずに数えて台帳へ残す。
            self.warmup += 1
            return
        if has_causal != has_full:
            # 規則 2: 片側にのみ点が存在する時刻（案 i が出し始めたあとの欠落）。
            side = "案 i" if has_causal else "案 ii"
            raise CausalityComparisonError(
                f"{series_name}: {side} にのみ点が存在します: bar={bar_time}"
            )
        if not has_causal:
            return  # 両側とも点なし＝比較対象外
        self.started = True
        self.compared += 1
        diff = _abs_diff(point.value, full_values[bar_time])
        if diff is None or diff > tolerance:
            if self.first_mismatch_time is None:
                self.first_mismatch_time = bar_time
        if diff is not None and diff > self.max_abs_diff:
            self.max_abs_diff = diff

    def to_finding(
        self,
        *,
        spec: IndicatorSpec,
        series_name: str,
        supply_seconds: "float | None",
        incomplete: "str | None",
    ) -> CausalityFinding:
        common = dict(
            spec=spec,
            series_name=series_name,
            bars_compared=self.compared,
            warmup_bars=self.warmup,
            max_abs_diff=self.max_abs_diff if self.compared else None,
            supply_seconds=supply_seconds,
        )
        if self.first_mismatch_time is not None:
            return CausalityFinding(
                selectable=False,
                reason=REASON_MISMATCH,
                detail=(
                    f"最初の不一致 time={self.first_mismatch_time} "
                    f"max_abs_diff={self.max_abs_diff}"
                ),
                first_mismatch_time=self.first_mismatch_time,
                **common,
            )
        if incomplete is not None:
            return CausalityFinding(
                selectable=False,
                reason=REASON_VERIFICATION_INCOMPLETE,
                detail=incomplete,
                **common,
            )
        if self.compared == 0:
            # 規則 6: 比較できたバーが 0 本。一致とは呼べない（測っていない）。
            return CausalityFinding(
                selectable=False,
                reason=REASON_VERIFICATION_INCOMPLETE,
                detail="比較できたバーが 0 本です（一致を主張できません）",
                **common,
            )
        return CausalityFinding(selectable=True, **common)


def _abs_diff(causal: "float | None", full: "float | None") -> "float | None":
    """規則 4 を含む差の算出。``None`` 同士は 0.0・片側だけ ``None`` は ``None``（不一致）。

    差が NaN になった場合も ``None``（＝比較できない＝不一致側）へ倒す。NaN をそのまま
    返すと ``nan > tolerance`` も ``nan > max_abs_diff`` も False になり、**全バー乖離
    していても selectable=True / max_abs_diff=0.0 と記録される**（fail-open）。
    現行 probe は NaN を ``None`` へ正規化するためここへは到達しないが、正規化は
    adapter の実装詳細であり、突合規則の側で塞ぐ（宣言でなく機械的に強制する）。
    """
    if causal is None and full is None:
        return 0.0
    if causal is None or full is None:
        return None
    diff = abs(float(causal) - float(full))
    return None if diff != diff else diff  # NaN 判定（math を持ち込まない）
