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

    # tick-store の戻り値型。実行時 pandas を import しない（依存方向維持）ため
    # 文字列注釈（"TickFrame"）として参照する。concrete 型は adapter 側で
    # pandas.DataFrame を採用する（設計 TBD・呼出側で後確定の判断点）。
    TickFrame = Any
    TickWriteResult = Any


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


class TickDataPort(abc.ABC):
    """ティック読込の隔離（Repository）。MarketDataPort は拡張しない（別 Port）。"""

    @abc.abstractmethod
    def load_ticks(
        self, symbol: str, start: Any, end: Any, columns: Any = None
    ) -> "TickFrame":
        """[start, end) 半開区間の tick を返す。該当なしは空 frame（例外でない）。"""
        raise NotImplementedError


class TickStorePort(abc.ABC):
    """ティック永続化の隔離（Repository）。raw を source of truth に日別へ振り分ける。"""

    @abc.abstractmethod
    def write_ticks(
        self, symbol: str, frame_or_csv: Any, mode: str = "overwrite"
    ) -> "TickWriteResult":
        """TICK_COLUMNS 準拠の tick を日別 Parquet へ書き込む（冪等）。

        mode="overwrite": 対象日を再生成 / mode="skip": 既存日を再書込しない。
        """
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


class SessionCalendarPort(abc.ABC):
    """市場開閉（セッション）の隔離。

    「新規成行を約定してはならないバー」の bar_index 集合を返す（事前計算・候補A）。
    実 MT5 は市場閉鎖時間帯（週末ギャップ隣接バー等）の成行を `[market closed]` で
    拒否し、開場する次バーで約定する。本ポートはその閉鎖バーを Interactor へ供給する。
    既定（NullCalendar）は空集合＝常時開場で、既定経路の出力を 1 バイトも変えない。
    """

    @abc.abstractmethod
    def closed_bar_indices(self, bars: Iterable["Bar"]) -> "set[int]":
        """新規成行を約定しないバー index の集合を返す（開場のみなら空集合）。"""
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
