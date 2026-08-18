"""NullTickModel: ティックを 1 つも生成しない `TickModelPort` 実装。

1. 層名/責務:
    adapter 層（約定・ティック生成）。どのバーに対しても空のティック列を返す
    TickModel。ティックを生成しない実行経路（Settings §8.2 の `Math calculations`）
    で `RunBacktestInteractor` へ注入する。合成ティックの発明は行わない。

2. 含む構造:
    NullTickModel（`TickModelPort.ticks_of` を空列で実装）。

3. 元 MQL 対応:
    MT5 の `Math calculations`（`Model=3`）は価格系列を供給せずティックを生成しない
    （基本設計 §4.5.2）。A-1（ISSUE-397・承認により方針反転）で
    `TICK_MODEL_REGISTRY` の ``math_calculations`` エントリの ``synthetic_builder``
    として登録した。これにより `main` の合成生成の既存 else がそのまま本実装を構築し、
    tick_model 側に新しい分岐が 1 つも増えない。

4. 依存:
    標準: typing
    外部: なし
    プロジェクト内: simulator.usecase.ports（TickModelPort）
"""
from __future__ import annotations

from typing import Any, Iterable

from simulator.usecase.ports import TickModelPort


class NullTickModel(TickModelPort):
    """常に空のティック列を返す（`TickModelPort` が明示的に許容する事後条件＝実読）。"""

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[Any]:
        return ()
