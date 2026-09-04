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


# =========================================================================== #
# ISSUE-261: バー秒長も台帳からの導出値である（手書き dict の第 2 定義を作らない）
# =========================================================================== #

def test_tf_bar_sec_is_derived_from_the_ledger():
    """``tf_meta.TF_BAR_SEC`` は台帳 ``TfDescriptor.bar_sec`` の写しではなく導出値である。

    かつては手書き dict で、検定も `set(TF_BAR_SEC) == set(TIMEFRAME_RULES)`（キー集合のみ）
    だったため**値のずれを検出できなかった**。台帳へ 1 行足せば自動で追随することを固定する。
    """
    from marketdata import tf_meta
    from marketdata.resample import TF_DESCRIPTORS

    assert tf_meta.TF_BAR_SEC == {c: d.bar_sec for c, d in TF_DESCRIPTORS.items()}
    # 順序も台帳と同一（挿入順に依存する消費者を非破壊にする）。
    assert list(tf_meta.TF_BAR_SEC.keys()) == list(TF_DESCRIPTORS.keys())


def test_adding_a_timeframe_to_the_ledger_updates_tf_bar_sec(monkeypatch):
    """台帳への追加が TF_BAR_SEC へ自動追随する（＝導出であり複製でない）。"""
    import importlib

    from marketdata import resample
    from marketdata.resample import TfDescriptor

    patched = dict(resample.TF_DESCRIPTORS)
    patched["2h"] = TfDescriptor("2h", True, False, 7200)
    monkeypatch.setattr(resample, "TF_DESCRIPTORS", patched)

    tf_meta = importlib.reload(importlib.import_module("marketdata.tf_meta"))
    try:
        assert tf_meta.TF_BAR_SEC["2h"] == 7200
    finally:
        monkeypatch.undo()
        importlib.reload(tf_meta)


def test_ledger_bar_sec_truth_table():
    """名目バー秒長の既知値（1W=7日・1M=30日名目）を固定する。"""
    from marketdata.resample import TF_DESCRIPTORS

    assert {c: d.bar_sec for c, d in TF_DESCRIPTORS.items()} == {
        "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
        "4h": 14400, "1D": 86400, "1W": 604800, "1M": 2592000,
    }
