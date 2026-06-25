"""CandleSource / TickSource ポート — 市場データ取得の境界（抽象）。

利用側（指標UI ツール・将来の dataset 供給）はこのポートにのみ依存する。具象（Dukascopy /
CSV 等）はこのポートを満たす adapter として差し替える（DIP）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - 型注釈専用（実行時 pandas import を強制しない）
    import pandas as pd


class Candle(TypedDict):
    """供給する 1 本の OHLCV。``time`` は解像度非依存の UNIX 秒（整数）。

    ``volume`` は tick volume または出来高（enabler①）。抽出元が値を持たない場合は
    ``0.0``（``cleaning`` / ``dukascopy_source`` が欠落を 0.0 で補う）。
    """

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@runtime_checkable
class CandleSource(Protocol):
    """OHLC candles の取得ポート。

    実装は ``[start, end)`` の candles を ``time`` 昇順の :class:`Candle` list で返す
    （データなしは空 list）。銘柄・足種・気配側などベンダ固有の設定は具象 adapter の
    構築時パラメータに隔離し、ポートの呼び出し面には出さない。
    """

    def fetch_candles(self, start: datetime, end: datetime) -> List[Candle]:
        ...


@runtime_checkable
class TickSource(Protocol):
    """実 tick の取得ポート（境界・抽象・enabler②）。

    実装は ``[start, end)`` の raw tick を ``timestamp`` 昇順の DataFrame で返す。
    timestamp は**列**に持つ（``reset_index`` 済・列名 ``"timestamp"``）。canonical 列
    （``timestamp/bid/ask/last/volume``）への変換は呼び出し側 service（ingest）が担い、
    ポートはベンダ raw（``ingest.RAW_COLUMNS`` 契約）を返す（last=mid 等の意味付けを
    port に焼かない）。気配側（offer_side）は単一指定せず bidPrice/askPrice 両列を常に
    返す（last=mid=(bid+ask)/2 算出を保全・H-3）。

    戻り値型に ``pandas.DataFrame`` を採るのは marketdata がインフラ境界であり pandas を
    技術ドライバとして許容するため（§3.2.1 案A・CandleSource が datetime を受けるのと同列）。
    """

    def fetch_ticks(self, start: datetime, end: datetime) -> "pd.DataFrame":
        ...
