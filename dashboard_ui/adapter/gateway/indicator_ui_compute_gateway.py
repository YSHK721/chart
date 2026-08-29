"""P-1 IndicatorSeriesPort / P-2 BarSupplyPort の実装（既存 `/compute` を read-only で読む）。

計算供給は `simulator.replay_ui.adapter._indicator_ui_bridge` の `full_compute` を
**in-process で読むだけ**である（replay / sim の前例と同形。HTTP でライブ core を叩かない
＝計算プールを奪わない・arch-spec §3）。指標の core は 1 行も変えない。

計算量の規律（§7・T-1・CLAUDE.md 絶対命令 §4.1）:
    同一 `(indicator_id, variant, params_key, timeframe)` の full 系列発行は **1 回以下**。
    P-1 は「1 呼出 = 1 計算 = 3 消費者（ラダー / 第 2 表 / 価格投影）で共有」の束契約なので、
    畳み込みはこの面が持つ。素材（DataFrame）も足ごとに 1 回だけ組み立てる。

技術隔離（CLEAN_ARCH §6）: pandas と indicator_ui はこのファイル（と同じ gateway 層）に
閉じる。usecase / domain は `dashboard_ui.usecase.sheet_ports` の Protocol 越しにしか
外を知らない。
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from marketdata.tf_meta import period_start_unix

from dashboard_ui.domain.bar import Bar

#: 系列 JSON の点の形（§6.3.2: time は UNIX 秒・value は float / 欠測は None）。
_DATA = "data"
_NAME = "name"
_TIME = "time"
_VALUE = "value"


class IndicatorUiComputeGateway:
    """P-1 / P-2 の実装。`bridge` は `_indicator_ui_bridge.load_compute()` の namespace。

    Args:
        bridge: dataset ＋ 計算面の namespace（None なら既定の bridge を遅延で解決する）。
        bar_limits: 足ごとに読む本数の上限。参照実装 `tools/measure/issue449/probe_inverse.py`
            の本数表と同じ役割で、費用の上限を素材の側で決める。None は全件。
    """

    def __init__(
        self, *, bridge: Any = None, bar_limits: "Mapping[str, int] | None" = None
    ) -> None:
        self._bridge = bridge
        self._bar_limits = dict(bar_limits or {})
        self._frames: "dict[tuple[str, str], Any]" = {}
        self._bars: "dict[tuple[str, str], tuple[Bar, ...]]" = {}
        self._series: "dict[tuple[str, str, str, str], Mapping[str, tuple]]" = {}

    # ------------------------------------------------------------------ P-1
    def full_series(
        self, *, indicator_id: str, variant: str, params: "Mapping[str, object]",
        dataset_ref: str, timeframe: str,
    ) -> "Mapping[str, tuple[tuple[int, float], ...]]":
        """系列名 → ((time, value), ...)。同一キーは 1 回しか計算しない。"""
        key = (indicator_id, variant, _params_key(params), timeframe)
        cached = self._series.get(key)
        if cached is not None:
            return cached
        bridge = self._resolve_bridge()
        frame = self._frame(dataset_ref, timeframe)
        series = bridge.full_compute(
            bridge.adapter, indicator_id, variant, frame, dict(params)
        )
        self._series[key] = _as_points(series)
        return self._series[key]

    # ------------------------------------------------------------------ P-2
    def bars(self, *, dataset_ref: str, timeframe: str) -> "tuple[Bar, ...]":
        """足の全件（時刻昇順）。"""
        key = (dataset_ref, timeframe)
        cached = self._bars.get(key)
        if cached is not None:
            return cached
        frame = self._frame(dataset_ref, timeframe)
        self._bars[key] = _as_bars(frame)
        return self._bars[key]

    def forming_bar(
        self, *, dataset_ref: str, timeframe: str, now_unix: int
    ) -> "Bar | None":
        """形成中の足（素材の末尾が現在の周期を覆っていなければ None）。

        供給が現在の周期に届いていないとき、古い足を「形成中」と偽らない（§5.2 水準なし・
        無言の縮退禁止）。周期の判定は `marketdata.tf_meta.period_start_unix` が唯一源で
        あり、ライブ側と同じ規約になる。
        """
        supplied = self.bars(dataset_ref=dataset_ref, timeframe=timeframe)
        if not supplied:
            return None
        last = supplied[-1]
        if period_start_unix(int(last.time), timeframe) != period_start_unix(
            int(now_unix), timeframe
        ):
            return None
        return last

    # ------------------------------------------------------------------ 内部
    def _resolve_bridge(self) -> Any:
        if self._bridge is None:
            from simulator.replay_ui.adapter import _indicator_ui_bridge  # 遅延: 技術隔離

            self._bridge = _indicator_ui_bridge.load_compute()
        return self._bridge

    def _frame(self, dataset_ref: str, timeframe: str) -> Any:
        """素材の DataFrame（足ごとに 1 回だけ組み立てる）。"""
        key = (dataset_ref, timeframe)
        cached = self._frames.get(key)
        if cached is not None:
            return cached
        bridge = self._resolve_bridge()
        if not bridge.dataset.is_known(dataset_ref):
            raise ValueError(f"未知の datasetRef です: {dataset_ref!r}")
        if not bridge.dataset.is_known_timeframe(timeframe):
            raise ValueError(f"未知の timeframe です: {timeframe!r}")
        frame = bridge.dataset.load_dataframe(dataset_ref, timeframe)
        limit = self._bar_limits.get(timeframe)
        self._frames[key] = frame if limit is None else frame.tail(int(limit))
        return self._frames[key]


def _params_key(params: "Mapping[str, object]") -> str:
    """畳み込みキーのパラメータ部（Input Model の畳み込みキーと同一規約＝決定的）。"""
    return json.dumps(dict(params), sort_keys=True, ensure_ascii=False, default=str)


def _as_points(
    series: "list[dict[str, Any]] | None",
) -> "Mapping[str, tuple[tuple[int, float], ...]]":
    """系列 JSON を `名前 → ((time, value), ...)` へ写す。

    同名の系列が複数返ることがある（実測: MA 乖離率は line と horizontal_line を同名で
    返す）。後から来た**空の系列で実体を上書きしない**——上書きすると水準が丸ごと落ちる。
    """
    points: "dict[str, tuple[tuple[int, float], ...]]" = {}
    for entry in series or []:
        name = entry.get(_NAME)
        if name is None:
            continue
        values = tuple(
            (int(point[_TIME]), float(point[_VALUE]))
            for point in (entry.get(_DATA) or [])
            if point.get(_VALUE) is not None
        )
        if values or name not in points:
            points[name] = values
    return points


def _as_bars(frame: Any) -> "tuple[Bar, ...]":
    """DataFrame → `Bar` の列（時刻は UNIX 秒。`candle.time` と同一符号化）。"""
    seconds = frame.index.values.astype("datetime64[s]").astype("int64").tolist()
    columns = {
        name: frame[name].to_numpy(dtype="float64").tolist()
        for name in ("open", "high", "low", "close")
    }
    volume = (
        frame["volume"].to_numpy(dtype="float64").tolist()
        if "volume" in frame.columns
        else [0.0] * len(seconds)
    )
    return tuple(
        Bar(time=int(time), open=open_, high=high, low=low, close=close, volume=vol)
        for time, open_, high, low, close, vol in zip(
            seconds, columns["open"], columns["high"], columns["low"],
            columns["close"], volume,
        )
    )
