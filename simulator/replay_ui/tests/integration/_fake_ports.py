"""replay_ui の統合検定で共有する「何も返さないポート」群（ISSUE-318）。

`serve_replay` の結線を検定するとき、検証対象でないポートは「呼ばれても空を返すだけ」で足りる。
その空実装を 7 本の統合検定が個別に手書きしており、同一定義が 6 箇所・6 箇所・5 箇所あった
（codescan 実測）。契約は 1 つなのでここに 1 つずつ置く。

各検定が持ち続けるもの: その検定が**観測したいポート**（呼出回数や引数を記録する Fake）。
本モジュールが提供するのは「検証対象外の口を黙らせる」ためのものだけである。
"""
from __future__ import annotations

from typing import Any


class FakeCandlePort:
    """ローソク取得ポートの空実装。"""

    def load_candles(self, ref: Any, timeframe: Any, limit: Any) -> list:
        return []


class FakeComputePort:
    """指標計算ポートの空実装。"""

    def load_source(self, ref: Any, timeframe: Any) -> list:
        return []

    def compute(self, indicator: Any, variant: Any, mode: Any, bars: Any, params: Any) -> list:
        return []


class FakeWindowPort:
    """窓データ（M1 行 / 生ティック）取得ポートの空実装。"""

    def load_m1_rows(self, ref: Any, start: Any, end: Any) -> list:
        return []

    def load_raw_ticks(self, start: Any, end: Any) -> list:
        return []
