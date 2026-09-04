"""NullIndicatorRegistry: 系列を 1 本も持たない `IndicatorPort` 実装。

1. 層名/責務:
    adapter 層（指標）。事前計算系列を一切保持しない registry。指標を必要としない
    実行経路（Settings §8.2 の `Math calculations` など）で `RunBacktestInteractor`
    へ注入する。系列の合成・既定値の供給は行わない。

2. 含む構造:
    NullIndicatorRegistry（`IndicatorPort` の `get` / `update` を実装）。

3. 元 MQL 対応:
    MT5 の `Math calculations` は価格系列を読まないため指標ハンドルも作られない
    （基本設計 §4.5.2）。本実装はその状態を Port 契約で表現したもの。

4. 依存:
    標準: typing
    外部: なし（pandas を import しない＝`PandasIndicatorRegistry` との差）
    プロジェクト内: simulator.usecase.ports（IndicatorPort）/
                    simulator.domain.exceptions（IndicatorBufferError）
"""
from __future__ import annotations

from typing import Any

from simulator.domain.exceptions import IndicatorBufferError
from simulator.usecase.ports import IndicatorPort


class NullIndicatorRegistry(IndicatorPort):
    """指標系列を 1 本も持たない registry（LSP: 空の `PandasIndicatorRegistry` と同挙動）。"""

    def get(self, name: str) -> Any:
        """常に `IndicatorBufferError`（未登録参照）。

        既存 `PandasIndicatorRegistry.get` の未登録時と同じ例外・同じ context
        （``name`` / ``available``）で失敗する（実読・置換可能性）。``None`` を返して
        沈黙させない——値が無いことを値で表すと、参照側が「0 の系列」と誤解する。
        """
        raise IndicatorBufferError(
            "未登録の指標参照", context={"name": name, "available": []}
        )

    def update(self, bar_index: int) -> None:
        """事前計算系列を持たないため no-op（既存 registry の `update` と同じ）。"""
        return None
