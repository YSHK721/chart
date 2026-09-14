"""UC へ渡す int 時刻ビューの単一ソース（tools 層・H-D1）。

`BuildReportPayload` は **int 時刻のみ**を受ける契約で、int 化は呼び出し側の
Composition Root が担う（`build_report_payload` docstring）。その「int 化」の実装が
2 か所に分かれていた:

    simulator/report_ui/tools/export_report_payload.py:62-113   （IS/OOS の実 run 出力）
    simulator/sim_ui/adapter/report_payload_writer.py:52-94     （sim ジョブの単一 run 出力）

codescan 実測（2026-08-11）でリポジトリ全体の clone 第 1 位（43 行・type-2）だった。
片方だけ直せば report.json が静かに食い違う（同じ payload を作る 2 経路なので、
食い違っても例外は出ない＝気づけない）。よって定義はここ 1 か所だけに置く。

bars / trades の時刻は供給源によって UNIX 秒 int・`numpy.int64`・`pandas.Timestamp`・
`numpy.datetime64` のいずれにもなる。その差を吸収するのが本モジュールの責務で、それ以外は
何もしない（値の解釈・写像の式は UC が持つ）。ただし**吸収規則そのものは本モジュールが
持たない**——規則の実体は `simulator.domain.bar_time.epoch_seconds` が唯一所有する
（ISSUE-412。手書きの型判定を置くと受理集合が二重定義になる）。
"""
from __future__ import annotations

from typing import Any

# 表現差の吸収規則は domain の単一ソースが唯一持つ（本モジュールは写しを作らない）。
from simulator.domain.bar_time import epoch_seconds


def unix_seconds(t: Any) -> int:
    """`numpy.datetime64` / epoch int（`numpy.int64` を含む）/ `Timestamp` を UNIX 秒へ（§4.3）。

    表現差の吸収規則は `simulator.domain.bar_time.epoch_seconds` が唯一持つ。本関数は
    その委譲だけを行い、型判定を書かない。未対応の表現は推測で解釈せず `ConfigError`
    （無音で 1970 年を出さない）。

    なぜ判定を書かないか（実測・ISSUE-412 (C)）:
        以前は `isinstance(t, int) and not isinstance(t, bool)` を手書きしていた。
        `isinstance(np.int64(1), int)` は **False**（numpy 2.4.6 実測）なので comma 形式
        CSV 由来の実型が分岐から外れ、`pd.Timestamp(np.int64(1776643200))` ＝ ns 解釈で
        **1970-01-01** に落ちていた（例外なしの桁ずれ）。判定を 1 か所に閉じることで
        この取り落としの発生源そのものを除去する。

    `bool` を int として素通ししない性質は `epoch_seconds` 側の受理集合が保つ
    （`isinstance(True, int)` は真だが時刻ではないため `ConfigError`）。

    `def` を残しているのは、本名が単一ソースの所有名だからである
    （`test_int_time_views_single_source.py` が定義の所在を AST で固定している）。
    """
    return epoch_seconds(t)


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
