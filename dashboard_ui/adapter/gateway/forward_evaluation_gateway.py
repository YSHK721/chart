"""P-3 ForwardEvaluationPort の実装（`forward(C) -> value`・指標の core は無改変）。

規律（§5.5.4・参照実装 tools/measure/issue449/probe_inverse.py:90-118）:
    窓は instance ごとに **1 回だけ複製**し、以降は形成中バー（末尾行）の代入だけを繰り返す。
    毎回 DataFrame を作り直すと 1 ステップの費用が窓長に比例する（ライブ側の毎ティック末尾値
    アダプタが同じ理由で同じ形を採っている）。終値候補を置いたときの走行極値は
    H = max(H0, 終値候補) / L = min(L0, 終値候補)
    （:meth:`dashboard_ui.domain.bar.RunningExtreme.extended_by` と同一規約）。

増分器を持たない指標:
    `latest_compute` は窓全体の再計算へ落ちる。前進評価は 1 instance あたり区分数 × 3 回
    呼ばれるため、そこへ落ちると納期に載らない。**黙って落ちない**で明示的に失敗させる
    （§7 無言の縮退禁止。ライブ側も `None` を返して純ロジック側で明示的に落としている）。

計算量（§7）: 前進評価はこの面からしか発行されない。Spy が数えるのはここだけである。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from dashboard_ui.adapter.gateway.param_scopes import ParamScopes, scopes_of
from dashboard_ui.domain.bar import RunningExtreme
from dashboard_ui.usecase.sheet_ports import ForwardEvaluationUnavailable

#: 系列 JSON の点の形（§6.3.2）。
_DATA = "data"
_NAME = "name"
_VALUE = "value"

#: 差し替える列（形成中バーの走行極値と終値）。始値・出来高は動かさない。
_CLOSE = "close"
_HIGH = "high"
_LOW = "low"


class MissingIncrementalError(ForwardEvaluationUnavailable):
    """前進評価に要る増分器が宣言されていない（＝この指標は価格投影の対象にできない）。

    P-3 の契約型（`ForwardEvaluationUnavailable`）を継承する。継承していないと、usecase は
    この失敗を「出せない instance」として扱えず、例外が controller の翻訳を貫通して
    **HTTP 応答が 1 つも返らなくなる**（レビュー 🟡-2 の実害）。
    """


class ForwardEvaluationGateway:
    """P-3 の実装。

    Args:
        value_series_of: `(indicator_id, variant, params) -> 系列名`。どの系列が「到達する量」
            かの宣言は第 2 表のセル宣言（役割判定表）が唯一源であり、本ゲートウェイは
            それを受け取るだけである（指標名の分岐を second source として持たない）。
        bridge: dataset ＋ 計算面の namespace（None なら既定の bridge を遅延で解決する）。
        bar_limits: 足ごとに読む本数の上限（None は全件）。
        is_incremental: 増分器の宣言有無（None ならライブ側の判定をそのまま使う）。
        param_scopes: variant ごとの受理 param 集合（ISSUE-466）。省略時はこの口の bridge
            から 1 回だけ読む。保持の寿命は Composition Root が決める。
    """

    def __init__(
        self,
        *,
        value_series_of: Callable[[str, str, "Mapping[str, object]"], str],
        bridge: Any = None,
        bar_limits: "Mapping[str, int] | None" = None,
        is_incremental: "Callable[[str, str, Mapping[str, object]], bool] | None" = None,
        param_scopes: "ParamScopes | None" = None,
    ) -> None:
        self._value_series_of = value_series_of
        self._bridge = bridge
        self._bar_limits = dict(bar_limits or {})
        self._is_incremental = is_incremental
        self._param_scopes = (
            param_scopes if param_scopes is not None
            else ParamScopes(source=lambda: scopes_of(self._resolve_bridge()))
        )
        self._windows: "dict[tuple[str, str, str], Any]" = {}
        self._running: "dict[tuple[str, str, str], RunningExtreme]" = {}

    def value_at_close(
        self, *, indicator_id: str, variant: str, params: "Mapping[str, object]",
        dataset_ref: str, timeframe: str, close: float,
    ) -> float:
        """終値候補 `close` を置いたときの当該バーの指標値。"""
        if not self._incremental(indicator_id, variant, params):
            raise MissingIncrementalError(
                f"増分器が宣言されていないため前進評価できません: "
                f"indicatorId={indicator_id!r} variant={variant!r}"
            )
        window = self._window(dataset_ref, timeframe, indicator_id)
        running = self._running[(dataset_ref, timeframe, indicator_id)].extended_by(close)
        _set_last_bar(window, close=float(close), high=running.high, low=running.low)

        bridge = self._resolve_bridge()
        # 発行の直前で受理集合へ絞る（ISSUE-466。P-1 と同じ規約・発行の口は 2 つある）。
        series = bridge.latest_compute(
            bridge.adapter, indicator_id, variant, window,
            self._param_scopes.scoped(
                indicator_id=indicator_id, variant=variant, params=params
            ),
        )
        name = self._value_series_of(indicator_id, variant, params)
        return _tail_value(series, name, indicator_id)

    # ------------------------------------------------------------------ 内部
    def _resolve_bridge(self) -> Any:
        if self._bridge is None:
            from indigators.indicator_ui import api_loader  # 遅延: 技術隔離

            self._bridge = api_loader.load_compute()
        return self._bridge

    def _incremental(
        self, indicator_id: str, variant: str, params: "Mapping[str, object]"
    ) -> bool:
        if self._is_incremental is None:
            self._resolve_bridge()
            from adapter.compute.live_tick_tails import is_incremental  # 遅延: 技術隔離

            self._is_incremental = is_incremental
        return bool(self._is_incremental(indicator_id, variant, dict(params)))

    def _window(self, dataset_ref: str, timeframe: str, indicator_id: str) -> Any:
        """instance ごとの窓（**1 回だけ**複製する）。

        指標ごとに別の複製を持つのは、末尾行を書き換えるからである（同じ窓を共有すると
        別の指標の評価が互いの末尾行を上書きし合う）。
        """
        key = (dataset_ref, timeframe, indicator_id)
        cached = self._windows.get(key)
        if cached is not None:
            return cached
        bridge = self._resolve_bridge()
        if not bridge.dataset.is_known(dataset_ref):
            raise ValueError(f"未知の datasetRef です: {dataset_ref!r}")
        if not bridge.dataset.is_known_timeframe(timeframe):
            raise ValueError(f"未知の timeframe です: {timeframe!r}")
        frame = bridge.dataset.load_dataframe(dataset_ref, timeframe)
        limit = self._bar_limits.get(timeframe)
        window = (frame if limit is None else frame.tail(int(limit))).copy()
        if len(window) == 0:
            raise ValueError(
                f"前進評価の窓が空です: datasetRef={dataset_ref!r} timeframe={timeframe!r}"
            )
        self._windows[key] = window
        self._running[key] = RunningExtreme(
            high=float(window[_HIGH].iloc[-1]), low=float(window[_LOW].iloc[-1])
        )
        return window


def _set_last_bar(window: Any, *, close: float, high: float, low: float) -> None:
    """窓の**末尾行だけ**を書き換える（pandas への依存を 1 か所へ寄せる）。"""
    last = len(window) - 1
    for column, value in ((_CLOSE, close), (_HIGH, high), (_LOW, low)):
        window.iloc[last, window.columns.get_loc(column)] = value


def _tail_value(series: Any, name: str, indicator_id: str) -> float:
    """宣言された系列の末尾値。無ければ明示エラー（黙って NaN を返さない）。"""
    for entry in series or []:
        if entry.get(_NAME) != name:
            continue
        data = entry.get(_DATA) or []
        if data and data[-1].get(_VALUE) is not None:
            return float(data[-1][_VALUE])
    raise ValueError(
        f"前進評価の値が得られません: indicatorId={indicator_id!r} series={name!r}"
    )
