"""データセットの Output Boundary（ISSUE-092 ①: DIP 逆転）。

usecase（方針側＝Application Business Rules）が所有する境界ポート。指標計算の業務手順は
datasetRef の物理格納（CSV / rollup parquet・resample キャッシュ＝偶有的性質）を知らず、
本ポートにのみ依存する。具象実装は :mod:`adapter.gateway.marketdata_dataset`
（marketdata.dataset 結線）が担い、エントリポイントは :func:`set_dataset_port` で
差し替えできる。

ISSUE-183（DIP 是正）: 従来は未注入時に本モジュールが
``from adapter.gateway.composition import default_dataset_port`` を関数スコープで実行していた
（内側 usecase が外側 adapter を import する逆流。行頭 import でないため回帰ガードの網を
すり抜けていた）。本モジュールは **具象も composition root も名指ししない**。既定合成は
composition root（:mod:`adapter.gateway.composition`）が :func:`set_default_dataset_port_factory`
で **押し込む**（push）形へ反転し、依存方向を「外側 → 内側」の一方向に揃える。

登録は各エントリポイントの責務:
  - 本番   : :mod:`framework.server` が import 時に ``install_default_ports()`` を 1 回呼ぶ。
  - テスト : ``api/tests/conftest.py`` が同じく 1 回呼ぶ。
未登録のまま :func:`dataset_port` が呼ばれた場合は「結線漏れ」として :class:`RuntimeError` を送出する
（欠落を serving 中の不定挙動へ先送りしない）。
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Protocol, runtime_checkable


@runtime_checkable
class RefValidationPort(Protocol):
    """datasetRef / timeframe のホワイトリスト解決だけを要するクライアント向けの狭いポート（ISP）。"""

    def is_known(self, ref: Any) -> bool:
        """``ref`` が既知の datasetRef キーかを返す（§7.3 パストラバーサル対策）。"""
        ...

    def is_known_timeframe(self, timeframe: Any) -> bool:
        """``timeframe`` が既知の時間足コードかを返す。"""
        ...


@runtime_checkable
class OhlcFramePort(Protocol):
    """指標計算用の OHLC DataFrame 供給だけを要するクライアント向けの狭いポート（ISP）。"""

    def load_dataframe(self, ref: str, timeframe: "str | None") -> Any:
        """解決済み datasetRef を OHLC DataFrame 互換で返す（timeframe で resample）。"""
        ...


@runtime_checkable
class CandleSeriesPort(Protocol):
    """配信用 candles 列（lightweight-charts 形）の供給だけを要するクライアント向けの狭いポート（ISP）。"""

    def load_candles(self, ref: str, timeframe: "str | None", limit: "int | None") -> Any:
        """解決済み datasetRef を candles JSON（§6.3）へ変換して返す。"""
        ...


@runtime_checkable
class DatasetPort(RefValidationPort, OhlcFramePort, Protocol):
    """/compute 用の合成ポート（ホワイトリスト解決 ＋ OHLC 供給）。

    メンバ集合は ISSUE-092 ① 当時と同一（``is_known`` / ``is_known_timeframe`` /
    ``load_dataframe``）で、``isinstance`` 判定の意味論を変えない。
    """


@runtime_checkable
class CandleDatasetPort(RefValidationPort, CandleSeriesPort, Protocol):
    """/candles・/forming_bar 用の合成ポート（ホワイトリスト解決 ＋ candles 供給・ISSUE-183 item6）。

    ISP: 配信系は ``load_dataframe``（指標計算用の DataFrame 化）を必要としないため、
    :class:`DatasetPort` とは別の合成面として定義する。既定 gateway は両面を実装する。
    """


_PORT: "Optional[DatasetPort]" = None
_DEFAULT_FACTORY: "Optional[Callable[[], DatasetPort]]" = None


def set_default_dataset_port_factory(
    factory: "Optional[Callable[[], DatasetPort]]",
) -> None:
    """既定 DatasetPort の合成関数を composition root から登録する（ISSUE-183）。

    本モジュールは具象も composition root も import しない。「どの具象が既定か」を知るのは外側
    （:mod:`adapter.gateway.composition`）であり、外側が本関数で押し込む。``None`` で登録解除。
    """
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = factory


def set_dataset_port(port: "Optional[DatasetPort]") -> None:
    """データセットポート実装を注入する（None で既定へ戻す）。合成はエントリポイントの責務。"""
    global _PORT
    _PORT = port


def dataset_port() -> DatasetPort:
    """現在のポートを返す。未注入なら登録済み既定 factory で合成する（自己完結起動の温存）。

    Raises:
        RuntimeError: ポート未注入かつ既定 factory 未登録（composition root の結線漏れ）。
    """
    global _PORT
    if _PORT is None:
        if _DEFAULT_FACTORY is None:
            raise RuntimeError(
                "DatasetPort が未結線です。エントリポイントで "
                "adapter.gateway.composition.install_default_ports() を呼ぶか、"
                "usecase.dataset_port.set_dataset_port(...) で注入してください。"
            )
        _PORT = _DEFAULT_FACTORY()
    return _PORT


def candle_dataset_port() -> CandleDatasetPort:
    """配信系（/candles・/forming_bar）向けのポート面を返す（ISSUE-183 item6）。

    単一の注入シーム（:func:`dataset_port`）へ委譲し、candles 供給のみを要するクライアントが
    ``load_dataframe`` を含まないポート型に依存できるようにする（参照実装
    ``market_profile_api.compute.tick_store_port`` の ``data_root`` / ``tick_reader`` と同規律）。
    """
    return dataset_port()  # type: ignore[return-value]  (既定 gateway は両面を実装する)
