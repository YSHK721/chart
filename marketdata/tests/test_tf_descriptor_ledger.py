"""TfDescriptor 単一台帳とその派生値の整合検証（ISSUE-134 OCP）。

カレンダー tf（1W/1M）の派生属性を複数箇所で再導出していた状態を、単一台帳
:data:`marketdata.resample.TF_DESCRIPTORS` からの導出値化へ構造変更した。本テストは
``TIMEFRAME_RULES`` / ``SESSION_TFS`` / ``NON_FLOORABLE_TF`` が台帳から導出された値であり、
台帳の内容（rule / floorable / calendar）が既知の真理値表と一致することを固定する
（新カレンダー足追加時に台帳 1 箇所の追記で全派生値が更新される OCP を担保）。
"""

from __future__ import annotations


def test_ledger_is_single_source_and_ordered():
    from marketdata.resample import TF_DESCRIPTORS, TIMEFRAME_RULES

    # 台帳キーは TIMEFRAME_RULES と同一・同順（順序依存の消費者を非破壊にするため）。
    assert list(TF_DESCRIPTORS.keys()) == list(TIMEFRAME_RULES.keys())


def test_timeframe_rules_is_derived_from_ledger():
    from marketdata.resample import TF_DESCRIPTORS, TIMEFRAME_RULES

    # dict[str, str|None] の互換ビュー: 各 rule は台帳の rule と一致。
    assert isinstance(TIMEFRAME_RULES, dict)
    assert TIMEFRAME_RULES == {code: d.rule for code, d in TF_DESCRIPTORS.items()}


def test_session_tfs_is_derived_from_calendar_flag():
    from marketdata.resample import SESSION_TFS, TF_DESCRIPTORS

    assert SESSION_TFS == tuple(c for c, d in TF_DESCRIPTORS.items() if d.calendar)
    assert SESSION_TFS == ("1D", "1W", "1M")


def test_non_floorable_tf_is_derived_from_floorable_flag():
    from marketdata.resample import TF_DESCRIPTORS
    from marketdata.tf_meta import NON_FLOORABLE_TF

    assert NON_FLOORABLE_TF == frozenset(
        c for c, d in TF_DESCRIPTORS.items() if not d.floorable
    )
    assert NON_FLOORABLE_TF == frozenset({"1W", "1M"})


def test_calendar_label_tfs_is_derived():
    """period_label_naive が扱う暦ラベル tf（calendar かつ非 floorable）＝{1W,1M}。"""
    from marketdata.resample import CALENDAR_LABEL_TFS, TF_DESCRIPTORS

    assert CALENDAR_LABEL_TFS == frozenset(
        c for c, d in TF_DESCRIPTORS.items() if d.calendar and not d.floorable
    )
    assert CALENDAR_LABEL_TFS == frozenset({"1W", "1M"})


def test_ledger_truth_table():
    from marketdata.resample import TF_DESCRIPTORS

    expected = {
        "1m": (None, True, False),
        "5m": ("5min", True, False),
        "15m": ("15min", True, False),
        "30m": ("30min", True, False),
        "1h": ("1h", True, False),
        "4h": ("4h", True, False),
        "1D": ("1D", True, True),
        "1W": ("W-FRI", False, True),
        "1M": ("ME", False, True),
    }
    got = {
        c: (d.rule, d.floorable, d.calendar) for c, d in TF_DESCRIPTORS.items()
    }
    assert got == expected
