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
    from simulator.domain.bar import Bar
    from simulator.domain.order import Order

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
        """OHLCFrame（本プロジェクトでは ``list[domain.Bar]``）を返す。

        ``source_ref`` 契約（ISSUE-135 LSP・全実装で対称）: データソースの所在を指す
        **パス様の参照**（CSV/TSV/parquet の各実装はファイルパスを受ける）。取得窓や銘柄など
        実装固有の選択軸は ``source_ref`` に載せず、各実装の**構築時パラメータへ隔離**する
        （例: marketdata 委譲実装は取得窓 (start,end) を構築時 window に保持し ``source_ref`` を
        参照しない）。これにより実装間で ``source_ref`` の事前条件が対称になり相互置換が可能。

        例外契約（全実装で対称）: I/O 失敗・データ取得失敗は生の外側例外を漏らさず
        ``DataError``（domain 例外）へ翻訳する。時刻昇順違反は ``TimeOrderError``、OHLC 整合
        違反は ``OHLCInvalidError``、必須列欠損は ``MissingBarError`` を送出する。
        """
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

    def on_tick(self, bar_index: int, bid: float, ask: float, account: Any) -> "list[Order]":
        """足途中ティック単位の発注フック（既定 no-op）。

        実 MT5 の OnTick 相当。ペンディング持続モード（config: pending_persistent）で、
        Interactor が「フラット かつ 未装填（保有0・resting 0）」のティックでのみ本メソッドを
        呼ぶ。当該ティックのクォート（bid/ask）でペンディングを装填して返す（StopEntryProbe の
        即時再アーム用）。非対応戦略は既定実装（空 list）でバー境界経路を維持する。
        """
        return []


class PositionManagerPort(abc.ABC):
    """建玉変更（トレーリング FR-07・部分決済 FR-08）の隔離（Phase 7）。

    Interactor は 1 評価点で保有玉 1 件を渡し、``PositionDirective``（新 SL/TP・部分決済量）
    または ``None``（無変更）を受け取る。既定（NullPositionManager）は常に ``None`` を返し、
    既定経路の出力を 1 バイトも変えない（LSP）。既存 StrategyPort は無変更（ISP）。

    ``granularity`` ∈ {"bar", "tick"}: 呼出元の評価粒度。トレーリング規則は自身の
    設定粒度と一致するときのみ作動する（両粒度を spec で選択）。適用順序（部分決済→
    SL/TP 更新→hit 判定の相対位置）は Interactor が符号化し、本 Port は値のみ返す。
    """

    @abc.abstractmethod
    def evaluate(
        self, *, ot: Any, ref_price: float, granularity: str, account: Any
    ) -> Any:
        """保有玉 1 件を評価し PositionDirective|None を返す。"""
        raise NotImplementedError


class StopOutPolicyPort(abc.ABC):
    """証拠金割れ（stop-out）が起きたときに何をするかの隔離（ISSUE-479 Wave2 4-4）。

    Interactor は「割れた」という事実（stop-out の文脈オブジェクト）だけを渡し、決定
    オブジェクトを受け取る。**例外の送出は Interactor が行う**——run を捨てるかどうかは実行の制御で
    あって方針オブジェクトの責務ではないため、方針は決定（強制決済するか否か）だけを
    返す。

    移設前はこの決定が `config.stop_out_action != "close_and_halt"` という比較として
    実行経路の 3 箇所（バー open 評価・バー close 評価・ティック評価）へ書き写されて
    いた。方針を増やすには 3 箇所すべてを直す必要があり（OCP 違反）、1 箇所を直し忘れると
    評価点によって違う方針で走る。本 Port は決定点を 1 つに閉じる。
    """

    @abc.abstractmethod
    def on_breach(self, ctx: Any) -> Any:
        """StopOutContext を受け StopOutDecision を返す。"""
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
        """Tick = (price, bid, ask, time) の列を返す。

        **空列を許容する**（ISSUE-135 LSP・全実装で対称の事後条件）: 当該バー区間に該当する
        ティックが無い場合は空の列を返す（例外でない）。合成実装（OhlcExpandTickModel /
        OpenOnlyTickModel / EveryTickModel）は常に非空だが、実ティック実装（RealTickModel）は
        バー区間 [bar.time, bar.time+足長) に実ティックが 0 件のとき空列を返しうる。呼出側
        （Interactor）は空列を「そのバスではティック無し」として扱い非空を前提にしない。
        """
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


class MarkdownReportPort(abc.ABC):
    """Markdown レポート表現の隔離（Presenter・UC-004）。

    形式別 1 メソッド Port（ISP・ISSUE-099 🟡-1）。Markdown 出力のみを担う実装が
    自 Port だけを履行できるようにする（LSP・ISSUE-098 🔴-1 の是正）。
    """

    @abc.abstractmethod
    def present_markdown(self, result: Any) -> str:
        raise NotImplementedError


class HtmlReportPort(abc.ABC):
    """HTML レポート表現の隔離（Presenter・UC-004）。形式別 1 メソッド Port（ISP）。"""

    @abc.abstractmethod
    def present_html(self, result: Any, path: Any) -> None:
        raise NotImplementedError


class JsonReportPort(abc.ABC):
    """JSON レポート表現の隔離（Presenter・UC-004）。形式別 1 メソッド Port（ISP）。"""

    @abc.abstractmethod
    def present_json(self, result: Any, path: Any) -> None:
        raise NotImplementedError


class ReportPresenterPort(MarkdownReportPort, HtmlReportPort, JsonReportPort):
    """3 形式 Port を束ねる後方互換の集約 Port（Presenter・UC-004）。

    形式別 Port（MarkdownReportPort / HtmlReportPort / JsonReportPort）へ分割済み。
    本 Port は 3 形式すべてを 1 つの実装で提供する消費者向けに温存する集約であり、
    3 メソッドすべてを abstract として継承する（各形式を全履行する実装のみ生成可能）。
    形式別 1 メソッドのみを担う Presenter は本集約ではなく対応する形式別 Port を実装する。
    """
