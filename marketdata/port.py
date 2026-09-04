"""CandleSource ポート — 市場データ取得の境界（抽象）。

利用側（指標UI ツール・将来の dataset 供給）はこのポートにのみ依存する。具象（Dukascopy /
CSV 等）はこのポートを満たす adapter として差し替える（DIP）。

ISSUE-092 ⑧: TickSource Protocol は抽象消費者ゼロ（YAGNI）につき撤去。ingest enabler② で
必要になれば再導入する（具象 DukascopyTickSource は現役・dukascopy_source に存置）。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Protocol, TypedDict, runtime_checkable


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

    実装は ``[start, end)`` の candles を :class:`Candle` list で返す。銘柄・足種・気配側など
    ベンダ固有の設定は具象 adapter の構築時パラメータに隔離し、ポートの呼び出し面には出さない。

    事後条件（全実装で対称・ISSUE-098 🟡-3／🟡-4 で明文化）:

    - **順序・一意性**: 返す candles は ``time`` **厳密昇順・一意**。同一 ``time`` が原データに
      複数あった場合は**後勝ち**（イテレーション順で最後の 1 本）で一意化する。これにより利用側
      （例: ``MarketDataSourceRepository`` の昇順ガード）での重複バー二重計上・index 重複・
      ``TimeOrderError`` を防ぐ。実装間で重複 time の扱いを対称に保つ（Dukascopy／CSV 同一）。
    - **データ不在**: ``[start, end)`` にデータが無い場合は**空 list** を返す。データ不在は誤り
      ではないため例外を送出しない。
    - **不正データ**: ``time`` を UNIX 秒 int として解釈できない不正データ（非 epoch 文字列・
      ``NaT`` 等）を検出した場合は ``ValueError`` を fail-fast 送出する（暗黙のフォールバックを
      設けない）。この例外契約も全実装で対称に保つ。
    - **``Candle.volume``**: 常に有限 ``float``。原データに volume が無い（列不在／セル欠損）
      場合は ``0.0`` で補う（Dukascopy／CSV 対称・ISSUE-102 🟡-1）。
    - **永続実体の不在（実装固有 I/O 例外）**: 構築時に固定した永続実体（CSV パス等）が存在
      しない場合、各実装は固有の I/O 例外を送出しうる（CSV は ``pandas`` 経由の
      ``FileNotFoundError``／ネットワーク系実装はベンダ層の接続例外）。これはデータ内容の
      事後条件ではなく**構成不整合の即時失敗**であり、無例外を前提にする利用側は置かない
      （利用側は捕捉境界を Composition Root 近傍で定義する・ISSUE-102 🔵-2）。
    """

    def fetch_candles(self, start: datetime, end: datetime) -> List[Candle]:
        ...
