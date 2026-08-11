"""案 i / 案 ii の系列取得（`CausalSeriesProbePort` 実装・adapter 層・Phase 3 F-5）。

基本設計書 §3.5.4 の 2 案を、**ライブと同一の計算経路**で取り出す:

    案 ii（`series_full`）  until_time までを 1 回計算した全系列の全点
    案 i （`series_upto`）  until_time で truncate して計算した全系列の「その時刻の点」

**指標式は再実装しない**。計算はリプレイ core の `causal_compute`（usecase）と
`CausalComputeGateway`（indicator_ui の `full_compute` を read-only 再利用する adapter）を
**無改変 import** して通す。写した瞬間にライブと 2 つの値が生まれる。

束（bundle）契約（裁定 A）: 1 回の計算は全系列を返す。系列ごとに問い合わせる契約だと、
同じ計算を系列数ぶん重複して払う（実測 2026-08-11: 母集合 26 組が 122 系列に多重化し
1 パス 138.5 秒）。返す単位を束にすることで、1 バーあたりの計算は 1 回で済む。

測定条件（Phase 3 構造設計 §絶対制約）:
    * ``limit=None`` 固定。tail で窓長が変わると EMA 系の seed 位置が変わり、
      実装差ではない不一致を作る。
    * 案 i も ``mode="full"``（probe_mode=full）。``latest`` は min_window tail での
      同値性が未検証のため既定にしない。
    * ``compute_timeframe`` は渡さない（チャート足での計算に固定し、案 i / 案 ii で
      同一の計算足を使う）。

CLEAN_ARCH §6: replay_ui / indicator_ui への依存は本ファイルに閉じる。
"""
from __future__ import annotations

from typing import Any

from simulator.replay_ui.usecase.causal_compute import (
    CausalComputeRequest,
    causal_compute,
)
from simulator.sim_ui.usecase.indicator_models import (
    IndicatorSpec,
    SeriesBundle,
    SeriesPoint,
)
from simulator.sim_ui.usecase.indicator_ports import CausalSeriesProbePort

#: レジストリの系列になる kind。これ以外（バンドの塗り・水平線・マーカー等）は
#:   「バー時刻に対応する値の列」ではないため供給できない。除外した名前は束が持つ
#:   （台帳へ記録し、「指標にその系列が無い」と区別する）。
SUPPLIED_KINDS = ("line", "histogram", "level_dash")


class CausalSeriesProbe(CausalSeriesProbePort):
    """ライブ compute を因果順守で叩く :class:`CausalSeriesProbePort` 実装。

    ``compute_port``: `CausalComputePort`（既定は `CausalComputeGateway`）。検定では
    フェイクを挿して呼び出しの形だけを固定できる。

    **源ロードの記憶（`MemoizedCausalComputePort`）はここで既定にしない**。裁定 B は
    その寿命を検定 CLI と 1 ジョブ子プロセスに限っており、常駐プロセスへ既定で載せると
    CSV 更新の検知が mtime 1 点に集約されて更新の見落としが古い値の供給になる。
    どの実装を包むかは合成根（`main/verify_indicator_causality_cli._default_probe`）が決める。
    """

    def __init__(
        self, *, compute_port: Any = None, api_path: Any = None, repo_root: Any = None
    ) -> None:
        self._api_path = api_path
        self._repo_root = repo_root
        self._compute_port = compute_port

    @property
    def compute_port(self) -> Any:
        """委譲先の `CausalComputePort`（記憶の効きを実測するための面）。"""
        return self._port()

    def _port(self) -> Any:
        if self._compute_port is None:
            # 遅延生成: indicator_ui の import を実際に使うときまで起こさない。
            from simulator.replay_ui.adapter.causal_compute_gateway import (
                CausalComputeGateway,
            )

            self._compute_port = CausalComputeGateway(self._api_path, self._repo_root)
        return self._compute_port

    # --- CausalSeriesProbePort -------------------------------------------

    def series_full(
        self,
        spec: IndicatorSpec,
        *,
        ref: str,
        timeframe: "str | None",
        until_time: "int | None",
    ) -> SeriesBundle:
        return _bundle(self._compute(spec, ref, timeframe, until_time))

    def series_upto(
        self,
        spec: IndicatorSpec,
        *,
        ref: str,
        timeframe: "str | None",
        until_time: int,
    ) -> "dict[str, SeriesPoint | None]":
        bundle = _bundle(self._compute(spec, ref, timeframe, int(until_time)))
        return {name: _tail_at(bundle[name], int(until_time)) for name in bundle}

    def bar_times(
        self, *, ref: str, timeframe: "str | None", count: int
    ) -> "list[int]":
        bars = self._port().load_source(ref, timeframe)
        times = [int(b["time"]) for b in bars]
        if isinstance(count, int) and count > 0:
            return times[:count]  # 窓の左端を動かさない（先頭から count 本）
        return times

    # --- 計算（ライブと同一経路）-----------------------------------------

    def _compute(
        self,
        spec: IndicatorSpec,
        ref: str,
        timeframe: "str | None",
        until_time: "int | None",
    ) -> "list[dict]":
        request = CausalComputeRequest(
            indicator=spec.indicator,
            variant=spec.variant,
            ref=ref,
            timeframe=timeframe,
            limit=None,          # 絶対制約: tail で窓長を変えない
            until_time=until_time,
            mode="full",         # probe_mode=full（latest は同値性未検証）
            forming=None,
            params=dict(spec.params or {}),
        )
        return causal_compute(request=request, compute_port=self._port())


def _bundle(payload: "list[dict]") -> SeriesBundle:
    """compute の pane 列 → :class:`SeriesBundle`（対象 kind のみ・除外名を保持）。

    系列名の重複は :class:`SeriesBundle` が拒否する（無音上書き禁止・単一実装）。
    """
    series: "list[tuple[str, list[SeriesPoint]]]" = []
    excluded: "list[str]" = []
    for pane in payload or []:
        name = str(pane.get("name"))
        if str(pane.get("kind")) not in SUPPLIED_KINDS:
            excluded.append(name)
            continue
        series.append((name, [
            SeriesPoint(time=int(p["time"]), value=_value_of(p.get("value")))
            for p in (pane.get("data") or [])
        ]))
    return SeriesBundle(series, excluded=excluded)


def _tail_at(points: "list[SeriesPoint]", until_time: int) -> "SeriesPoint | None":
    """系列の末尾点（時刻が ``until_time`` でなければ「その時刻の値は無い」）。"""
    if not points:
        return None
    tail = points[-1]
    return tail if int(tail.time) == until_time else None


def _value_of(value: Any) -> "float | None":
    """compute の値を正規化する（``None`` と NaN はどちらも未定義点＝``None``）。"""
    if value is None:
        return None
    number = float(value)
    return None if number != number else number  # NaN 判定（math を持ち込まない）
