"""計算量 14（ISSUE-512 段階 1）: 表示時点への巻き戻しのティック読取を、浪費させない。

用語（初出定義）:
    _as_of_display（dashboard_ui/adapter/gateway/indicator_ui_compute_gateway.py:216）
        ＝ 素材を表示時点（now − 遅延秒）へ巻き戻すメソッド。ティックへ触る呼び出しを
          2 つ持つ: mod.forming_bar（遅延時点の形成中バーを tick から畳む）と
          mod.apply_forming_bar（その形成中バーを末尾へ set/replace する）。
    TickTokenMissing（marketdata/dataset_registry.py）
        ＝ datasetRef が tick=True なのに tick_token が未記入のときに台帳が送出する例外。

なぜ状態検証では落ちないか:
    ティック読取を素材の本数ぶん発行しても、また台帳の記入漏れで止まった後に続きの読取を
    発行して捨てても、**出力は完全に正しい**。値で見るかぎり区別がつかない
    （ISSUE-450 と同型）。回数を数える検査だけがこれを落とせる。

固定する不変量（いずれも **無駄の不在** であって回数ではない）:
    1. 素材の本数を変えても、ティック読取の発行数は変わらない（O(1) の表明・2 点で固定）。
    2. 巻き戻しが台帳の記入漏れで止まったとき、その後のティック読取を発行しない
       （止まった後に作って捨てる計算が無い）。

CLAUDE.md 絶対命令 §4.1: 測るのは時間ではなく回数。回数そのものは期待値へ焼き込まない。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dashboard_ui.adapter.gateway.indicator_ui_compute_gateway import (
    IndicatorUiComputeGateway,
)
from dashboard_ui.tests.unit.test_indicator_ui_compute_gateway import (
    REF,
    START,
    RewindSpy,
    frame,
)
from marketdata.dataset_registry import TickTokenMissing


class TickReadSpy(RewindSpy):
    """ティック読取の 2 つの窓口が呼ばれた回数を控える bridge（Test Spy）。

    `stop_at_fold` を立てると mod.forming_bar が台帳の記入漏れで止まる。
    """

    def __init__(self, frames, *, forming=None, stop_at_fold=False) -> None:
        super().__init__(frames, forming=forming)
        self.folds = 0
        self.injections = 0
        self._stop_at_fold = stop_at_fold

    def namespace(self) -> SimpleNamespace:
        ns = super().namespace()
        inner, spy, stop = ns.forming_bar_module, self, self._stop_at_fold

        class CountingFormingModule:
            @staticmethod
            def forming_bar(ref, tf, cutoff):
                spy.folds += 1
                if stop:
                    raise TickTokenMissing("datasetRef 'zz' は tick_token が未記入です")
                return inner.forming_bar(ref, tf, cutoff)

            @staticmethod
            def apply_forming_bar(df, ref, tf, cutoff, *, synthesize_closed_gaps):
                spy.injections += 1
                return inner.apply_forming_bar(
                    df, ref, tf, cutoff, synthesize_closed_gaps=synthesize_closed_gaps
                )

        ns.forming_bar_module = CountingFormingModule()
        return ns


#: 遅延時点の形成中バー（これが None だと注入側の読取へ到達しない）。
def _forming_at(rows: int) -> dict:
    return {"time": START + (rows - 3) * 60, "open": 1.0, "high": 2.0,
            "low": 0.5, "close": 1.5, "volume": 10.0}


def test_the_tick_reads_do_not_grow_with_the_material_length() -> None:
    """オーダーの表明（2 点固定）: 素材 120 本と 3000 本で、ティック読取の発行数は同じ。"""
    # Arrange / Act
    issued = {}
    for rows in (120, 3000):
        spy = TickReadSpy({"1m": frame(rows)}, forming=_forming_at(rows))
        cutoff_now = START + (rows - 3) * 60 + 12
        gateway = IndicatorUiComputeGateway(
            bridge=spy.namespace(), now=lambda: cutoff_now,
        )

        gateway.bars(dataset_ref=REF, timeframe="1m")
        issued[rows] = (spy.folds, spy.injections)

    # Assert
    assert issued[120][0] > 0, "ティック読取が 1 度も走っていない（検定が空振りしている）"
    assert issued[120] == issued[3000], (
        f"素材を 120→3000 本に増やしたらティック読取の発行が"
        f" {issued[120]}→{issued[3000]} に変わった（素材本数に比例させてはならない）"
    )


def test_a_ledger_omission_stops_before_issuing_the_injection_read() -> None:
    """台帳の記入漏れで止まった後、注入側のティック読取を発行しない（捨てる計算 0）。

    止めたうえで続きを読むと、読んだ結果は必ず捨てられる（例外で出口へ抜けるため）。
    出力は例外なので状態検証では区別できない。
    """
    # Arrange
    spy = TickReadSpy({"1m": frame(120)}, forming=_forming_at(120), stop_at_fold=True)
    gateway = IndicatorUiComputeGateway(
        bridge=spy.namespace(), now=lambda: START + 117 * 60 + 12,
    )

    # Act
    with pytest.raises(TickTokenMissing):
        gateway.bars(dataset_ref=REF, timeframe="1m")

    # Assert
    assert spy.folds > 0, "巻き戻しの読取へ到達していない（検定が空振りしている）"
    assert spy.injections == 0, (
        f"記入漏れで止まった後に注入側の読取を {spy.injections} 回発行している"
        "（結果は必ず捨てられる＝作って捨てている）"
    )
