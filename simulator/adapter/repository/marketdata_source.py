"""MarketDataSourceRepository — marketdata.CandleSource へ委譲し Candle→domain.Bar 写像する
MarketDataPort 実装（S5・strangler 委譲経路）。

設計正典: MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md §2.3（Candle→Bar 写像規則）/ §3.3（class/
method 図・昇順ガード）/ §10.1 C-2（source_ref=(start,end) 半開）/ §10.2 H-4（spread=0 は
spread 非依存戦略のみ＝既定 TC・WeeklyVolBand）。

クリーンアーキ依存方向（厳守）: 本 adapter は usecase（MarketDataPort abc）＋domain（Bar /
例外）＋marketdata（CandleSource 境界ポート）に依存する。**simulator usecase は本 adapter を
import しない**（DIP: usecase は MarketDataPort abc にのみ依存）。Candle→Bar 写像と昇順／OHLC
検証（domain.Bar.__post_init__）を本 adapter に閉じる。

委譲範囲（H-4）: spread 非依存戦略（comma 形式・既定 TC・WeeklyVolBand）に限定する。spread
依存戦略（MA_Slope / MA_Slope_Pending / StopEntryProbe）は委譲対象外＝既存 Mt5CsvOHLCRepository
を維持する（composition root が ea_name 別に振り分ける）。

ISSUE-135（LSP 是正）: MarketDataPort.load の source_ref を path 系 3 実装（CSV/TSV/parquet）
と対称化する。本実装固有の選択軸である取得窓 (start,end) は **構築時パラメータ（window）へ
隔離** し、load は source_ref をアンパックしない（path 文字列を受理・置換可能）。例外契約も
path 系 3 実装と対称化し、fetch 段の失敗（永続実体不在の I/O 例外・CandleSource の fail-fast
ValueError 等）は生例外を漏らさず DataError へ翻訳する（写像 domain 検証は翻訳対象外）。
"""
from __future__ import annotations

from typing import Any

from marketdata import CandleSource
from simulator.domain.bar import Bar
from simulator.domain.exceptions import DataError, TimeOrderError
from simulator.usecase.ports import MarketDataPort


def _candles_to_bars(candles: Any) -> list[Bar]:
    """Candle 列を domain.Bar 列へ写像する（§2.3 規則・昇順／OHLC 検証つき）。

    time は int 直渡し、open/high/low/close は float 化、volume は ``c.get("volume", 0.0)``、
    spread は ``0`` 既定（H-4: spread 非依存戦略のみ）。OHLC 整合は ``Bar.__post_init__`` が、
    時刻昇順は本関数のガードが検証する（``frame_to_bars`` と同一の検証点）。
    """
    bars: list[Bar] = []
    prev_time = None
    for c in candles:
        # OHLC 整合違反は domain.Bar が OHLCInvalidError を送出（内側例外・翻訳不要）。
        bar = Bar(
            time=c["time"],
            open=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=float(c.get("volume", 0.0)),
            spread=0,
        )
        if prev_time is not None and bar.time <= prev_time:
            raise TimeOrderError(
                "時刻が昇順ではありません",
                bar_index=len(bars),
                context={"prev_time": str(prev_time), "time": str(bar.time)},
            )
        prev_time = bar.time
        bars.append(bar)
    return bars


class MarketDataSourceRepository(MarketDataPort):
    """marketdata.CandleSource へ委譲し Candle→domain.Bar 写像する MarketDataPort 実装。"""

    def __init__(self, source: CandleSource, *, window: Any) -> None:
        # DI: 構築時に CandleSource と取得窓 (start,end) を注入する（ISSUE-135）。取得窓は
        # 本実装固有の選択軸のため構築時へ隔離し、load の source_ref は path 系実装と対称化する。
        self._source = source
        self._window = window  # (start, end)（半開・C-2）

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> list[Bar]:
        """構築時窓 ``(start, end)``（半開・C-2）を fetch_candles へ委譲し Bar 列へ写像する。

        ``source_ref`` は MarketDataPort 契約上受けるが本実装では未使用（path 系 3 実装と対称・
        置換可能）。取得窓は構築時 ``window`` から解決する（ISSUE-135 LSP）。

        例外契約（path 系 3 実装と対称・ISSUE-135）: fetch 段の失敗（永続実体不在の I/O 例外・
        CandleSource の fail-fast ValueError 等）は DataError へ翻訳し元例外を chain する。写像段
        の domain 検証（OHLCInvalidError / TimeOrderError）は翻訳せず送出する（``frame_to_bars``
        が read_csv の外で検証を送出するのと対称）。
        """
        start, end = self._window
        try:
            candles = self._source.fetch_candles(start, end)
        except Exception as exc:  # marketdata 層の I/O / データ検証例外を内側へ翻訳
            raise DataError(
                f"市場データの取得に失敗しました: {start}..{end}",
                context={"start": str(start), "end": str(end), "cause": repr(exc)},
            ) from exc
        return _candles_to_bars(candles)
