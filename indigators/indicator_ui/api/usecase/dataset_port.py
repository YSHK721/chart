"""データセットの Output Boundary（ISSUE-092 ①: DIP 逆転）。

usecase（方針側＝Application Business Rules）が所有する境界ポート。指標計算の業務手順は
datasetRef の物理格納（CSV / rollup parquet・resample キャッシュ＝偶有的性質）を知らず、
本ポートにのみ依存する。具象実装は :mod:`adapter.gateway.marketdata_dataset`
（marketdata.dataset 結線）が担い、エントリポイントは :func:`set_dataset_port` で
差し替えできる。

未注入時は既定実装を composition root（:mod:`adapter.gateway.composition`）から遅延合成する。
これは「自己完結起動の温存」であり、usecase からの module-level marketdata / adapter 依存は
排除される（型契約は本ポートが唯一）。参照実装 market_profile_api.compute.tick_store_port と
同じ規律に従う。ISSUE-137: 既定具象名（``MarketdataDatasetGateway``）は composition root へ集約し、
本ポートには具象クラス名を持たせない。
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class DatasetPort(Protocol):
    """datasetRef ホワイトリスト解決と OHLC 供給の抽象（read-only）。"""

    def is_known(self, ref: Any) -> bool:
        """``ref`` が既知の datasetRef キーかを返す（§7.3 パストラバーサル対策）。"""
        ...

    def is_known_timeframe(self, timeframe: Any) -> bool:
        """``timeframe`` が既知の時間足コードかを返す。"""
        ...

    def load_dataframe(self, ref: str, timeframe: "str | None") -> Any:
        """解決済み datasetRef を OHLC DataFrame 互換で返す（timeframe で resample）。"""
        ...


_PORT: "Optional[DatasetPort]" = None


def set_dataset_port(port: "Optional[DatasetPort]") -> None:
    """データセットポート実装を注入する（None で既定へ戻す）。合成はエントリポイントの責務。"""
    global _PORT
    _PORT = port


def dataset_port() -> DatasetPort:
    """現在のポートを返す。未注入なら composition root の既定を遅延合成する（自己完結起動）。"""
    global _PORT
    if _PORT is None:
        from adapter.gateway.composition import default_dataset_port

        _PORT = default_dataset_port()
    return _PORT
