"""供給ポートと例外分類の検定（ISSUE-447 段階 1）。

障害の種類ごとに「再試行してよいか」が違う。認証・端末・過負荷は待てば直りうるが、引数不正は
待っても直らない。ここを 1 つの例外にまとめると、直らない要求を延々と投げ続けるか、
直る障害で常駐ループを落とすかのどちらかになる。分類は本モジュールが唯一の判断点。
"""
from __future__ import annotations

import pytest

from marketdata.mt5_ticks import port
from marketdata.mt5_ticks.port import Mt5SupplyError, SupplyUnavailable


def test_retryable_failures_are_a_subclass_of_the_supply_error():
    """再試行可否は型で区別する（呼び出し側が文字列を読んで判断しない）。"""
    assert issubclass(SupplyUnavailable, Mt5SupplyError)


@pytest.mark.parametrize("status", [401, 429, 502])
def test_transient_statuses_map_to_a_retryable_error(status):
    """認証・過負荷・端末不調はバックオフ対象（設計 §4）。"""
    err = port.error_for_status(status, {}, b"")
    assert isinstance(err, SupplyUnavailable)


def test_argument_errors_are_fail_stop_and_never_retried():
    """400 は待っても直らない。再試行対象にしない（設計 §4）。"""
    err = port.error_for_status(400, {}, b"bad symbol")
    assert isinstance(err, Mt5SupplyError)
    assert not isinstance(err, SupplyUnavailable)


def test_terminal_failures_carry_the_last_error_detail():
    """E-8: 端末側の ``last_error`` が失われない（原因の特定を諦めない）。"""
    err = port.error_for_status(502, {}, b'{"error":"terminal","last_error":[-10005,"boom"]}')
    assert "boom" in str(err)


def test_unknown_statuses_are_fail_stop_rather_than_silently_retried():
    """未知の応答を「待てば直る」と決めつけない（無限再試行を作らない）。"""
    err = port.error_for_status(418, {}, b"")
    assert isinstance(err, Mt5SupplyError)
    assert not isinstance(err, SupplyUnavailable)


def test_the_incremental_tick_source_protocol_is_runtime_checkable():
    """DI 差替（http / fake / spy の 3 実装）を型で確認できる。"""

    class _Source:
        def fetch(self, *, symbol, from_msc, to_msc, max_rows):
            return None

    assert isinstance(_Source(), port.IncrementalTickSource)
    assert not isinstance(object(), port.IncrementalTickSource)


def test_the_clock_protocol_is_runtime_checkable():
    class _Clock:
        def now(self):
            return None

    assert isinstance(_Clock(), port.Clock)
    assert not isinstance(object(), port.Clock)
