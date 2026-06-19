"""TickModel 実装（TickModelPort・PROCESS §0.2/§7-#1・CLEAN_ARCH §6.3）。

ticks_of(bar, prev_close) -> Iterable[Tick]   # Tick = (price, bid, ask, time)

    OhlcExpandTickModel: 1 バーを O→H→L→C の 4 疑似ティックへ展開（決定論・§7-#5）。
    OpenOnlyTickModel  : 始値のみ（1 ティック）。
    EveryTickModel     : 実ティック列。OHLC のみの入力では O→H→L→C 近似へフォール
                         バック（実ティック供給は将来の Dukascopy gateway＝範囲外）。

最小骨格: spread=0 のとき bid=ask=price（実 spread は spread_model 接続時に拡張）。
Tick は標準 tuple（フレームワーク型を漏らさない）。
"""
from __future__ import annotations

from typing import Any, Iterable

from backtest.usecase.ports import TickModelPort


def _tick(price: float, bar: Any) -> tuple:
    # spread=0 の最小骨格: bid=ask=price。Tick = (price, bid, ask, time)
    half = getattr(bar, "spread", 0) / 2.0
    return (price, price - half, price + half, bar.time)


def _ohlc_ticks(bar: Any) -> Iterable[tuple]:
    for price in (bar.open, bar.high, bar.low, bar.close):
        yield _tick(price, bar)


class OhlcExpandTickModel(TickModelPort):
    """O→H→L→C の 4 疑似ティックへ展開する。"""

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        return _ohlc_ticks(bar)


class OpenOnlyTickModel(TickModelPort):
    """始値のみ（1 ティック）。"""

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        return [_tick(bar.open, bar)]


class EveryTickModel(TickModelPort):
    """実ティック列。OHLC のみの入力では O→H→L→C 近似へフォールバックする。"""

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        return _ohlc_ticks(bar)


def _to_domain_time(ts: Any) -> Any:
    """frame の timestamp を domain の time 型（numpy.datetime64）へ正規化する。

    pandas.Timestamp を usecase/domain へ漏らさない（Bar.time 契約: pd.Timestamp 禁止・
    numpy.datetime64 | int）。pandas.Timestamp は to_datetime64() で datetime64 化する。
    """
    to_dt64 = getattr(ts, "to_datetime64", None)
    return to_dt64() if to_dt64 is not None else ts


def _normalize_bar_time(bar_time: Any) -> Any:
    """バー区間算定用に bar.time を numpy.datetime64 へ正規化する（epoch int は不変）。

    Bar.time 契約は numpy.datetime64 | int だが、CSV ローダ（CsvOHLCRepository）は
    ISO 文字列 time を「そのまま」採用するため、real_ticks 経路では bar.time が
    str / pandas.Timestamp になり得る（ISSUE-016）。区間算定は時刻演算を要するため、
    時刻系（str / numpy.datetime64 / pandas.Timestamp）は datetime64 へ寄せ、epoch int
    は算術可能なため不変で返す。pandas/numpy は本 adapter 内に閉じる。
    """
    import numpy as np

    if isinstance(bar_time, bool):  # bool は int サブクラス。時刻でないので除外
        return bar_time
    if isinstance(bar_time, int):  # epoch int はそのまま算術可能
        return bar_time
    if isinstance(bar_time, np.datetime64):
        return bar_time
    # str / pandas.Timestamp / その他時刻表現は datetime64 へ正規化する。
    return np.datetime64(bar_time)


def _bar_end(bar_time: Any) -> Any:
    """バー区間 [bar.time, bar.time+足長) の終端を返す（M1=60s 前提）。

    bar_time は _normalize_bar_time 済（numpy.datetime64 または epoch int）を前提とする。
    numpy.datetime64 なら timedelta64(60,"s")、epoch int なら +60 を加算する。足長は M1 固定。
    """
    import numpy as np

    if isinstance(bar_time, np.datetime64):
        return bar_time + np.timedelta64(60, "s")
    return bar_time + 60


class RealTickModel(TickModelPort):
    """実ティック frame からバー区間の実ティックを整形する（every-tick #4）。

    保持する canonical frame（timestamp/bid/ask/last/volume）から、当該バー区間
    [bar.time, bar.time+足長=60s) の実ティックを timestamp 昇順で
    Tick=(price=last, bid, ask, time=timestamp) へ整形する。区間 0 件は空を返す。
    pandas は本 adapter 内に隔離し usecase へ漏らさない。prev_close は契約上受けるが
    整形では未使用。
    """

    def __init__(self, frame: Any) -> None:
        # timestamp 昇順へ安定ソートする（レビュー 🟡-1・順序ハザード是正）。
        # every-tick は順序依存（最初tick=約定価格・tick列順=SL/TP/stop-out 発火順）で
        # あり、frame 行順が非ソートでも ticks_of が常に時刻順を返すことを不変条件と
        # して保証する。mergesort（安定）で同一 timestamp の相対順序を保つ。period frame
        # 一括保持中ゆえ追加メモリは実質なし。
        self._frame = frame.sort_values("timestamp", kind="mergesort")

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        ts = self._frame["timestamp"]
        # 半開区間 [bar.time, bar.time+60s) で決定論的にスライスする。bar.time は CSV 由来で
        # ISO 文字列になり得る（ISSUE-016）ため区間算定用に datetime64 へ正規化する。
        bar_start = _normalize_bar_time(bar.time)
        mask = (ts >= bar_start) & (ts < _bar_end(bar_start))
        sliced = self._frame.loc[mask]
        return [
            (row.last, row.bid, row.ask, _to_domain_time(row.timestamp))
            for row in sliced.itertuples(index=False)
        ]
