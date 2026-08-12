"""UC へ渡す int 時刻ビューの単一ソース（tools 層・H-D1）。

`BuildReportPayload` は **int 時刻のみ**を受ける契約で、int 化は呼び出し側の
Composition Root が担う（`build_report_payload` docstring）。その「int 化」の実装が
2 か所に分かれていた:

    simulator/report_ui/tools/export_report_payload.py:62-113   （IS/OOS の実 run 出力）
    simulator/sim_ui/adapter/report_payload_writer.py:52-94     （sim ジョブの単一 run 出力）

codescan 実測（2026-08-11）でリポジトリ全体の clone 第 1 位（43 行・type-2）だった。
片方だけ直せば report.json が静かに食い違う（同じ payload を作る 2 経路なので、
食い違っても例外は出ない＝気づけない）。よって定義はここ 1 か所だけに置く。

bars / trades の時刻は供給源によって UNIX 秒 int・`pandas.Timestamp`・`numpy.datetime64`
のいずれにもなる。その差を吸収するのが本モジュールの責務で、それ以外は何もしない
（値の解釈・写像の式は UC が持つ）。
"""
from __future__ import annotations

from typing import Any


def unix_seconds(t: Any) -> int:
    """`numpy.datetime64` / epoch int / `Timestamp` を UNIX 秒 int へ正規化する（§4.3）。

    pandas の import を関数内に置くのは、int 時刻だけを扱う経路を pandas の読み込みへ
    巻き込まないため（sim core は int 時刻だけの経路を持つ）。

    `bool` を int として素通ししない。`isinstance(True, int)` は真なので、素通しすると
    `time=1`（1970 年）のバーが黙って混ざる。
    """
    if isinstance(t, int) and not isinstance(t, bool):
        return t
    import pandas as pd

    return int(pd.Timestamp(t).timestamp())


class IntTimeBar:
    """UC の excursion 用に bar.time を int 化した read-only ビュー（high/low/open/close）。"""

    __slots__ = ("time", "high", "low", "open", "close")

    def __init__(self, bar: Any) -> None:
        self.time = unix_seconds(bar.time)
        self.high = bar.high
        self.low = bar.low
        self.open = bar.open
        self.close = bar.close


class IntTimeTrade:
    """UC 用に entry_time/exit_time を int 化した read-only TradeRecord ビュー。

    `pnl()` は構築時に 1 回だけ読んだ値を返す（UC が何度も呼ぶため）。
    """

    __slots__ = ("side", "entry_time", "exit_time", "entry_price", "exit_price",
                 "volume", "exit_reason", "_pnl")

    def __init__(self, tr: Any) -> None:
        self.side = tr.side
        self.entry_time = unix_seconds(tr.entry_time)
        self.exit_time = unix_seconds(tr.exit_time)
        self.entry_price = tr.entry_price
        self.exit_price = tr.exit_price
        self.volume = tr.volume
        self.exit_reason = tr.exit_reason
        self._pnl = tr.pnl()

    def pnl(self) -> float:
        return self._pnl


class ResultView:
    """UC が読む BacktestResult の int 時刻ビュー（trades/balance_curve/stats を read-only 写像）。

    `deals` / `equity_curve` は `BacktestResult` の必須フィールド（`usecase/models.py:150-151`）
    なので素直に読む。`getattr` の既定値で受けると、属性を持たない入力まで通ってしまい、
    本番の型契約をテストダブルへ合わせることになる（移設で契約を緩めない）。
    """

    def __init__(self, result: Any) -> None:
        self.trades = [IntTimeTrade(t) for t in result.trades]
        self.balance_curve = list(result.balance_curve)
        self.stats = result.stats
        self.deals = result.deals
        self.equity_curve = result.equity_curve
