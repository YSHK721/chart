"""NullStrategy: 発注も決済もしない `StrategyPort` 実装（Settings §8.2 Math calculations）。

1. 層名/責務:
    adapter 層（戦略）。売買判断を一切行わない戦略。`Model=3`（Math calculations）の
    実行のように「EA のロジックを走らせずにテスターの実行そのものを成立させる」
    経路で `RunBacktestInteractor` へ注入する。値の発明・統計の生成は行わない。

2. 含む構造:
    NullStrategy（`StrategyPort` の 3 メソッドを no-op で実装）。

3. 元 MQL 対応:
    MT5 の `Math calculations` は価格系列を供給せず OnTick を呼ばない（基本設計
    §4.5.2）。本実装はその「売買イベントが 1 度も起きない」状態を Port 契約で
    表現したものであり、特定の `.mq5` に 1:1 対応する実体は無い。

4. 依存:
    標準: typing
    外部: なし
    プロジェクト内: simulator.usecase.ports（StrategyPort）

既定実装の先例（実測）: `NullCalendar`（adapter/calendar/session_calendar.py:27）・
`NullPositionManager`（adapter/position_manager/position_manager.py:24）と同じく、
Port ABC を継承した「何もしない」実装を技術関心のディレクトリへ置く。ABC 継承に
より、Interactor が新しい呼出点を増やしたときの実装漏れは生成時の `TypeError` と
して現れる（実行途中の `AttributeError` にならない＝Fail-Stop）。
"""
from __future__ import annotations

from typing import Any

from simulator.usecase.ports import StrategyPort

#: 保有玉を触らないことを表す `on_position_check` の戻り値。既存戦略 7 本
#: （TC24051901 / MaSlope / MaSlopePending / StopEntryProbe / WeeklyVolBand /
#: ProFitBand / GenericConditionStrategy）がすべてこの値を返す（実読）。語彙を
#: 発明せず既存実装から採る。
HOLD: str = "hold"


class NullStrategy(StrategyPort):
    """発注も決済も行わない戦略（LSP: 既存戦略と置換可能・出力は常に空）。"""

    def on_init(self, config: Any, indicators: Any) -> None:
        """初期化で何もしない（Interactor はループ前に本メソッドを必ず呼ぶ＝実読）。"""
        return None

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list":
        """新規発注を出さない（空列）。"""
        return []

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        """保有玉を触らない（``"hold"``）。保有玉は生じないため通常は呼ばれない。"""
        return HOLD
