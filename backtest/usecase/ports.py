"""usecase 層の境界（ポート）抽象（CLEAN_ARCH §5）。

Output Boundary（DIP で実装を外側へ追い出す）と Input Boundary（UC の入口）を
``abc.ABC`` で定義する。戻り値にフレームワーク型（pandas/lightweight-charts 等）を
漏らさない方針をシグネチャで表明する（例: StrategyPort.on_new_bar は list[Order]）。

usecase 層は domain のみ依存可。本モジュールは型注釈のために domain のみ参照する。
"""
from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # 型注釈専用（実行時 import 不要・domain のみ）
    from backtest.domain.bar import Bar
    from backtest.domain.order import Order


# ---- Input Boundary ----

class RunBacktestInputBoundary(abc.ABC):
    """UC-001: バックテストを 1 run 実行する入口。"""

    @abc.abstractmethod
    def execute(self, request: Any) -> Any:
        """RunBacktestRequest を受け BacktestResult を返す。"""
        raise NotImplementedError


class CompareStatsInputBoundary(abc.ABC):
    """UC-003: MT5 結果と突合する入口。"""

    @abc.abstractmethod
    def execute(self, py_stats: Any, mt5_stats: dict, tolerances: dict) -> Any:
        """ComparisonReport を返す。"""
        raise NotImplementedError


# ---- Output Boundary ----

class MarketDataPort(abc.ABC):
    """データ取得の隔離（Repository）。"""

    @abc.abstractmethod
    def load(self, source_ref: Any, timeframe: Any, period: Any) -> Any:
        """OHLCFrame を返す。"""
        raise NotImplementedError


class ResultSinkPort(abc.ABC):
    """永続化の隔離（Repository）。"""

    @abc.abstractmethod
    def save_trades(self, df: Any, path: Any) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def save_stats(self, stats: dict, path: Any) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def save_report(self, html: str, path: Any) -> None:
        raise NotImplementedError


class StrategyPort(abc.ABC):
    """戦略の隔離（EAStrategy）。戻り値に pandas 型を漏らさず Order を返す。"""

    @abc.abstractmethod
    def on_init(self, config: Any, indicators: Any) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        raise NotImplementedError

    @abc.abstractmethod
    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        """"hold" または "close" を返す。"""
        raise NotImplementedError


class IndicatorPort(abc.ABC):
    """指標の隔離（IndicatorRegistry）。"""

    @abc.abstractmethod
    def get(self, name: str) -> Any:
        """指標系列を返す（未登録参照は IndicatorBufferError）。"""
        raise NotImplementedError

    @abc.abstractmethod
    def update(self, bar_index: int) -> None:
        raise NotImplementedError


class TickModelPort(abc.ABC):
    """ティック生成の隔離（PROCESS §0.2/§7-#1）。"""

    @abc.abstractmethod
    def ticks_of(self, bar: "Bar", prev_close: float) -> Iterable[Any]:
        """Tick = (price, bid, ask, time) の列を返す。"""
        raise NotImplementedError


class ReportPresenterPort(abc.ABC):
    """レポート表現の隔離（Presenter・UC-004）。"""

    @abc.abstractmethod
    def present_markdown(self, result: Any) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def present_html(self, result: Any, path: Any) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def present_json(self, result: Any, path: Any) -> None:
        raise NotImplementedError
