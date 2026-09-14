"""RSI（profit_rsi）の区分の境目 — 適用価格の折れ ＋ 上下分岐（区分 6）。

実測（§5.5.2・参照実装 tools/measure/issue449/probe_inverse.py）: v(C) の区分数は
marod 系の 3 に対して **6** である。増えるぶんは RSI の差分 x_t − x_{t-1} の符号で
式が変わる**上下分岐**であり、境目は x(C) = x_{t-1} を満たす終値候補である。

x(C)（適用価格）は走行 H / L で折れる区分アフィンなので、3 区分それぞれで 1 次方程式を
解いて候補を 1 つずつ得る（折れ 2 点 ＋ 候補 3 点 ＝ 5 つの境目 ＝ 6 区分）。区分の外に出た
候補も落とさない: その候補はメビウスの区分を細分するだけで当てはめは厳密なままであり、
「どちらの区分に属するか」の境界規則を第 2 の場所で持たずに済む（区分数も実測と一致する）。

適用価格の写像は **指標自身の apply 写像**（core の APPLY_TO_PRICE）を唯一の源とする
（写しを作らない）。apply=5 は TYPICAL（hlc3）であり、共有の適用価格列挙の値 5（MEDIAN）
とは別物である。
"""
from __future__ import annotations

import importlib
from typing import Callable, Mapping

import numpy as np
from common.applied_price import applied_price

from dashboard_ui.domain.bar import Bar, RunningExtreme

#: 同一とみなす境目の丸め桁（区分メビウスの当てはめが行う重複除去と同じ桁）。
_ROUND_DIGITS: int = 9

#: 区分アフィンの傾きを 2 点から読むための探針間隔（区分内に必ず収まる相対位置で使う）。
_SLOPE_PROBES: "tuple[float, float]" = (0.25, 0.75)

#: これ未満しか離れていない境目は同一点として畳む（区分メビウスの最小区分幅と同値）。
_MIN_GAP: float = 1e-6

#: 既定の apply（指標 core の既定値と同値。写像そのものは core から引く）。
_DEFAULT_APPLY: int = 5


def _apply_to_price(apply: int):
    """指標 core の apply 写像を read-only で解決する（写しを持たない）。

    指標 src の読み込みは indicator_ui の唯一の入口（`indicator_src`）へ委譲する。探索パスの
    用意は `indigators.indicator_ui.api_loader` が唯一源（replay / sim と同形の
    read-only 再利用。第 2 の sys.path 操作を書かない）。
    """
    from indigators.indicator_ui import api_loader  # 遅延: 技術隔離

    api_loader.load_compute()
    from adapter.compute.call_binding import indicator_src  # 遅延: 技術隔離を本層に閉じる

    src = indicator_src("profit_rsi")
    core = importlib.import_module(src.__name__ + ".core")
    return core.APPLY_TO_PRICE(int(apply))


def default_applied_price(bar: Bar, close: float, params: "Mapping[str, object]") -> float:
    """終値候補 `close` を置いたときの適用価格（走行 H / L は `Bar` の規約に従う）。"""
    running = RunningExtreme.of(bar).extended_by(close)
    kind = _apply_to_price(int(params.get("apply", _DEFAULT_APPLY)))
    value = applied_price(
        kind,
        np.asarray([bar.open], dtype=np.float64),
        np.asarray([running.high], dtype=np.float64),
        np.asarray([running.low], dtype=np.float64),
        np.asarray([close], dtype=np.float64),
    )
    return float(np.asarray(value)[0])


class ProfitRsiBreakpoints:
    """P-4 実装（折れ ＋ 上下分岐）。

    Args:
        applied_price: `(bar, close, params) -> 適用価格`。既定は指標自身の `apply` 写像。
            注入点を開けてあるのは、適用価格の解決を差し替えるためではなく、境目の算術だけを
            指標ランタイム無しで検定できるようにするためである。
    """

    def __init__(
        self,
        applied_price: "Callable[[Bar, float, Mapping[str, object]], float] | None" = None,
    ) -> None:
        self._applied_price = applied_price or default_applied_price

    def previous_value(self, *, bar: Bar, params: "Mapping[str, object]") -> float:
        """その足の適用価格（上下分岐の高さ＝前バーの `x_{t-1}`）。"""
        return float(self._applied_price(bar, float(bar.close), params))

    def breakpoints(
        self, *, bar: Bar, params: "Mapping[str, object]", prev_value: "float | None"
    ) -> "tuple[float, ...]":
        """区分の境目（昇順・重複なし）。"""
        running = RunningExtreme.of(bar)
        cuts = [float(running.low), float(running.high)]
        if prev_value is not None:
            cuts.extend(self._branch_candidates(bar, params, float(prev_value)))
        return _folded(cuts)

    # ------------------------------------------------------------------ 内部
    def _branch_candidates(
        self, bar: Bar, params: "Mapping[str, object]", prev_value: float
    ) -> "list[float]":
        """適用価格 = prev_value を折れの 3 区分それぞれで解く。

        適用価格は区分内でアフィンなので、2 点の評価で傾きと切片が厳密に決まる（前進評価は
        1 回も発行しない＝ここは適用価格の算術だけで閉じる）。
        """
        running = RunningExtreme.of(bar)
        span = max(running.high - running.low, 1.0)
        regions = (
            (running.low - 4.0 * span, running.low),
            (running.low, running.high),
            (running.high, running.high + 4.0 * span),
        )
        solutions: "list[float]" = []
        for lo, hi in regions:
            if hi - lo < _MIN_GAP:
                continue
            first, second = (lo + (hi - lo) * position for position in _SLOPE_PROBES)
            y_first = float(self._applied_price(bar, first, params))
            y_second = float(self._applied_price(bar, second, params))
            slope = (y_second - y_first) / (second - first)
            if slope == 0.0:
                continue          # その区分で適用価格が終値候補に依らない（分岐点が無い）
            intercept = y_first - slope * first
            solutions.append((prev_value - intercept) / slope)
        return solutions


def _folded(cuts: "list[float]") -> "tuple[float, ...]":
    """昇順に並べ、`_MIN_GAP` 未満しか離れていない境目を 1 点へ畳む。

    畳まないと区分メビウスの当てはめが幅の足りない区分を捨て、どの区分にも属さない価格
    （＝価格から指標値を引けない穴）が生まれる。
    """
    kept: "list[float]" = []
    for cut in sorted(round(float(value), _ROUND_DIGITS) for value in cuts):
        if kept and cut - kept[-1] < _MIN_GAP:
            continue
        kept.append(cut)
    return tuple(kept)
