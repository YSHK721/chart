"""marod 系（MA 乖離率・トレンド乖離率）の区分の境目 — 適用価格の折れ（区分 3）。

実測（§5.5.2・参照実装 tools/measure/issue449/probe_inverse.py）: 前進評価 v(C) の区分は
C < L / L <= C <= H / C > H の 3 つであり、境目は**走行 H / L**である。終値候補 C が
走行高値を越えれば高値も動き、走行安値を割れば安値も動くため、適用価格（hlc3 等）の傾きが
その 2 点で折れる（:class:`~dashboard_ui.domain.bar.RunningExtreme` が持つ max/min 規約と同一）。

marod 系に**上下分岐は無い**（`prev_value` は境目に寄与しない）。分岐が加わるのは
RSI（差分の符号で式が変わる）だけであり、そちらは別モジュールが持つ。
"""
from __future__ import annotations

from typing import Mapping

from dashboard_ui.domain.bar import Bar, RunningExtreme

#: 同一とみなす境目の丸め桁（区分メビウスの当てはめが行う重複除去と同じ桁）。
_ROUND_DIGITS: int = 9


class MarodBreakpoints:
    """P-4 実装。走行 H / L の 2 点（同値なら 1 点）を返す。"""

    def previous_value(
        self, *, bar: Bar, params: "Mapping[str, object]"
    ) -> "float | None":
        """上下分岐の高さ。marod 系は分岐を持たないため常に None（LSP: 面は揃える）。"""
        return None

    def breakpoints(
        self, *, bar: Bar, params: "Mapping[str, object]", prev_value: "float | None"
    ) -> "tuple[float, ...]":
        """区分の境目（昇順・重複なし）。

        Args:
            bar: 形成中の足（走行 H / L を持つ）。
            params: 指標パラメータ（適用価格の種別は折れの**位置**を変えないため参照しない。
                走行 H / L に依らない適用価格〔`open` 等〕では折れが縮退するが、その場合も
                同じ区分を細分するだけで当てはめは厳密なままである）。
            prev_value: 前バーの適用価格（marod 系では境目に寄与しない）。
        """
        running = RunningExtreme.of(bar)
        cuts = {round(float(running.low), _ROUND_DIGITS),
                round(float(running.high), _ROUND_DIGITS)}
        return tuple(sorted(cuts))
