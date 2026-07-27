"""DatasetPort の marketdata 実装（ISSUE-092 ①）。

datasetRef ホワイトリスト解決と OHLC 供給（marketdata.dataset＝旧 adapter.compute.dataset の
実体）を usecase から隔離する具象 gateway。挙動は従来の直 import と同一（is_known /
is_known_timeframe / load_dataframe を等価委譲する）。

``marketdata.dataset`` はモジュールオブジェクトへ実行時委譲する（属性を束縛しない）。これに
より既存テストの ``dataset.load_dataframe`` monkeypatch 経路が gateway 経由でも温存される
（同一モジュールオブジェクトの属性差し替えを呼出時に解決するため）。
"""
from __future__ import annotations

from typing import Any

from marketdata import dataset as _dataset


class MarketdataDatasetGateway:
    """marketdata.dataset への等価委譲で DatasetPort / CandleDatasetPort を実装する。"""

    def is_known(self, ref: Any) -> bool:
        return _dataset.is_known(ref)

    def is_known_timeframe(self, timeframe: Any) -> bool:
        return _dataset.is_known_timeframe(timeframe)

    def load_dataframe(self, ref: str, timeframe: "str | None") -> Any:
        return _dataset.load_dataframe(ref, timeframe)

    def load_candles(self, ref: str, timeframe: "str | None", limit: "int | None") -> Any:
        """配信用 candles 列を返す（ISSUE-183 item6: /candles も DIP 経由へ統一）。

        従来 controller が ``marketdata.dataset.load_candles`` を直呼びしていたものと等価委譲。
        モジュールオブジェクトへ実行時委譲するため既存の monkeypatch 経路も温存される。
        """
        return _dataset.load_candles(ref, timeframe, limit)
