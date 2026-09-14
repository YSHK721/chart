"""バックテストエンジン ドメイン例外階層（DESIGN §9.1）。

すべての例外は :class:`BacktestError` を基底とし、共通属性 ``context``（任意の
診断情報を保持する dict）を持つ。階層は次の 4 系統に分かれる。

    BacktestError
    ├── ConfigError
    ├── DataError
    │   ├── MissingBarError
    │   ├── OHLCInvalidError
    │   └── TimeOrderError
    ├── IndicatorError
    │   ├── IndicatorNaNError
    │   └── IndicatorBufferError
    └── ExecutionError
        ├── InvalidPriceError
        └── MarginCallError

domain 層は外部ゼロ依存（標準ライブラリのみ）。
"""
from __future__ import annotations


class BacktestError(Exception):
    """全ドメイン例外の基底。診断用の 4 属性を保持する（DESIGN §9.2/§9.3）。

    ``timestamp`` / ``symbol`` / ``bar_index`` / ``context`` の 4 属性を任意
    キーワード引数として受け取り、省略時は None（context は空 dict）とする。
    すべて任意引数のため既存の呼び出し・例外階層との後方互換を保つ（🟡-1）。

    ``timestamp`` の型は ``numpy.datetime64 | int | None``（pd.Timestamp 禁止・
    domain 層は numpy のみ依存可。設計書の pd.Timestamp 型注釈は外側前提の名残の
    ため不採用）。
    """

    def __init__(
        self,
        message: str,
        *,
        context: dict | None = None,
        symbol: str | None = None,
        bar_index: int | None = None,
        timestamp=None,
    ) -> None:
        super().__init__(message)
        self.context = context or {}
        self.symbol = symbol
        self.bar_index = bar_index
        self.timestamp = timestamp


class ConfigError(BacktestError):
    """設定不正（起動前検出）。"""


class DataError(BacktestError):
    """入力データ起因の例外。"""


class MissingBarError(DataError):
    """欠損足。"""


class OHLCInvalidError(DataError):
    """OHLC 矛盾（high < low 等）。"""


class TimeOrderError(DataError):
    """時刻昇順違反。"""


class IndicatorError(BacktestError):
    """指標計算起因の例外。"""


class IndicatorNaNError(IndicatorError):
    """NaN がシグナル評価必要箇所に出た。"""


class IndicatorBufferError(IndicatorError):
    """バッファ参照不正。"""


class ExecutionError(BacktestError):
    """約定・口座起因の例外。"""


class InvalidPriceError(ExecutionError):
    """SL/TP 価格制約違反（stops_level 等）。"""


class MarginCallError(ExecutionError):
    """ストップアウト。"""
