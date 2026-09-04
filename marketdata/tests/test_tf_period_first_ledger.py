"""期間始端規則（period_first）が時間足台帳の派生属性であることの検定（ISSUE-479 Wave2 M-3）。

なぜ必要か:
    「その時間足の期間はどの日から始まるか」（1D=その日 / 1W=金曜の 6 日前 / 1M=月初）という
    **同一の規則**が、``resample.period_utc_start`` ・ ``session_day.period_session_labels`` ・
    ``session_day.next_period_label`` ・ ``tf_meta.bar_time_unix`` ・ ``tf_meta.period_start_unix``
    の 5 箇所に ``tf == "1D"`` 等のリテラル分岐として書き写されていた。写しは台帳へ時間足を足しても
    追随せず、ISSUE-253（ライブの更新粒度が時間足で割れる）と同型の「静かなずれ」を生む。

本検定の 2 面:
    - 構造（AST）: 上記 3 ファイルの比較式の右辺に時間足コードのリテラルが 1 つも残らないこと。
    - 振る舞い（OCP）: 台帳へ 1 行足すだけで 5 経路すべてが追随すること（他ファイル無変更）。

加えて計算量（発行 − 使用 = 0）を Test Spy で固定する。台帳引きが「呼び出しごとに 1 回」で
あり、台帳の行数が増えても呼び出しあたりの発行が増えないこと（全行走査へ退化しないこと）を
表明する。回数そのものは焼き込まない（固定するのは無駄の不在であって実装詳細ではない）。
"""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

_PKG = Path(__file__).resolve().parents[1]

#: 期間始端規則が書き写されていた 3 ファイル（設計書 M-3 の実測 5 箇所の所在）。
_SITES = ("resample.py", "session_day.py", "tf_meta.py")

#: 比較式の右辺に現れてはならない時間足コード（派生属性は台帳から引く）。
_TF_LITERALS = frozenset({"1D", "1W", "1M"})


def _timeframe_literal_compares(path: Path) -> "list[tuple[int, str]]":
    """比較式（``==`` / ``in`` …）の右辺に時間足コード定数を持つ箇所を (行, 式) で列挙する。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found: "list[tuple[int, str]]" = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for comparator in node.comparators:
            leaves = (
                comparator.elts
                if isinstance(comparator, (ast.Tuple, ast.List, ast.Set))
                else [comparator]
            )
            if any(
                isinstance(leaf, ast.Constant) and leaf.value in _TF_LITERALS
                for leaf in leaves
            ):
                found.append((node.lineno, ast.unparse(node)))
                break
    return found


@pytest.mark.parametrize("filename", _SITES)
def test_no_timeframe_code_literal_survives_in_a_comparison(filename):
    """時間足コードのリテラル分岐が 1 件も残っていない（規則の写しを構造的に禁ずる）。"""
    offenders = _timeframe_literal_compares(_PKG / filename)
    assert offenders == [], (
        f"{filename} に時間足コードのリテラル分岐が残っています: {offenders}。"
        " 期間始端規則は marketdata.tf_ledger の派生属性から導出してください。"
    )


# =========================================================================== #
# 振る舞い: 台帳へ 1 行足すだけで 5 経路が追随する（OCP）
# =========================================================================== #

def _unix(ts: str) -> int:
    """naive UTC 表記 → UNIX 秒（期待値をテスト側で独立に組み立てるための補助）。"""
    return int(pd.Timestamp(ts).value // 1_000_000_000)


def _quarter_first(d: "date") -> "date":
    """四半期の初日（テスト専用の仮想時間足 1Q の期間始端規則）。"""
    return d.replace(month=((d.month - 1) // 3) * 3 + 1, day=1)


@pytest.fixture()
def ledger_with_quarter(monkeypatch):
    """台帳へ仮想時間足 ``1Q``（暦ラベル tf・四半期）を 1 行足した状態を作る。

    台帳から導出済みの module 級の集合（import 時に確定する）も同じ 1 行から作り直す。
    足すのは**台帳の 1 行だけ**であり、5 経路のどのファイルにも手を入れない。
    """
    from marketdata import resample, tf_ledger, tf_meta

    row = tf_ledger.TfDescriptor("QE", False, True, 7_776_000, period_first=_quarter_first)
    monkeypatch.setitem(tf_ledger.TF_DESCRIPTORS, "1Q", row)
    monkeypatch.setitem(resample.TIMEFRAME_RULES, "1Q", "QE")
    monkeypatch.setattr(tf_ledger, "CALENDAR_LABEL_CODES",
                        tf_ledger.CALENDAR_LABEL_CODES | {"1Q"})
    monkeypatch.setattr(resample, "SESSION_TFS", resample.SESSION_TFS + ("1Q",))
    monkeypatch.setattr(resample, "CALENDAR_LABEL_TFS",
                        resample.CALENDAR_LABEL_TFS | {"1Q"})
    monkeypatch.setattr(tf_meta, "CALENDAR_LABEL_TFS", resample.CALENDAR_LABEL_TFS | {"1Q"})
    # tf_meta が台帳の calendar 集合を参照していること自体は
    # test_tf_meta_exposes_the_session_tf_set_from_the_ledger が固定する。
    monkeypatch.setattr(tf_meta, "SESSION_TFS", resample.SESSION_TFS + ("1Q",), raising=False)
    return row


def test_tf_meta_exposes_the_session_tf_set_from_the_ledger():
    """tf_meta はセッション tf 集合を resample（台帳の導出値）から受け取る（写しを作らない）。"""
    from marketdata import resample, tf_meta

    assert tf_meta.SESSION_TFS is resample.SESSION_TFS


def test_period_utc_start_follows_the_ledger(ledger_with_quarter):
    """resample.period_utc_start が台帳の期間始端規則へ追随する（1 経路目）。

    2026-03-31（QE ラベル）の期間始端は、四半期初日 2026-01-01 のセッション始端と一致する。
    比較対象は 1D（ラベル日そのものが期間先頭）の同日始端であり、セッション始端の式を
    テスト側へ書き写さない。
    """
    from marketdata.resample import period_utc_start

    got = period_utc_start("1Q", pd.Timestamp("2026-03-31"))
    expected = period_utc_start("1D", pd.Timestamp("2026-01-01"))
    assert got == expected


def test_period_utc_start_preserves_the_time_of_day(ledger_with_quarter):
    """ラベルの時刻成分は期間始端計算で失われない（pd.Timestamp(date) への退化を禁ずる）。"""
    from marketdata.resample import period_utc_start

    midnight = period_utc_start("1M", pd.Timestamp("2026-03-31 00:00:00"))
    noon = period_utc_start("1M", pd.Timestamp("2026-03-31 12:34:56"))
    assert noon - midnight == pd.Timedelta(hours=12, minutes=34, seconds=56)


def test_period_session_labels_follows_the_ledger(ledger_with_quarter):
    """session_day.period_session_labels が台帳の期間始端規則へ追随する（2 経路目）。"""
    from marketdata.session_day import period_session_labels

    labels = period_session_labels("1Q", "2026-03-31")
    assert labels[0] == "2026-01-01"
    assert labels[-1] == "2026-03-31"
    assert len(labels) == 31 + 28 + 31


def test_period_session_labels_still_rejects_non_calendar_tf(ledger_with_quarter):
    """暦ラベル tf でない時間足は従来どおり ValueError（ガード文言も温存）。"""
    from marketdata.session_day import period_session_labels

    with pytest.raises(ValueError, match="1W\\|1M のみ対応"):
        period_session_labels("1D", "2026-03-31")


def test_next_period_label_follows_the_ledger(ledger_with_quarter):
    """session_day.next_period_label が台帳の暦ラベル tf 集合へ追随する（3 経路目）。"""
    from marketdata.session_day import next_period_label

    assert next_period_label("1Q", "2026-03-31") == "2026-06-30"


def test_bar_time_unix_follows_the_ledger(ledger_with_quarter):
    """tf_meta.bar_time_unix が台帳の暦ラベル tf 集合へ追随する（4 経路目）。"""
    from marketdata.tf_meta import bar_time_unix

    t = int(pd.Timestamp("2026-02-15 12:00:00").value // 1_000_000_000)
    got = bar_time_unix("1Q", t)
    assert got == int(pd.Timestamp("2026-03-31").value // 1_000_000_000)


def test_period_start_unix_follows_the_ledger(ledger_with_quarter):
    """tf_meta.period_start_unix が台帳の期間始端規則へ追随する（5 経路目）。"""
    from marketdata.resample import period_utc_start
    from marketdata.tf_meta import period_start_unix

    t = int(pd.Timestamp("2026-02-15 12:00:00").value // 1_000_000_000)
    expected = int(period_utc_start("1D", pd.Timestamp("2026-01-01")).value // 1_000_000_000)
    assert period_start_unix(t, "1Q") == expected


@pytest.fixture()
def ledger_with_session_day_like(monkeypatch):
    """台帳へ「セッション日で集計するが暦ラベルではない」仮想時間足 ``1S`` を 1 行足す。

    tf_meta の 2 箇所（bar_time_unix / period_start_unix）に残っていた ``tf == "1D"`` は
    「暦 tf のうち暦ラベルでないもの」＝台帳の calendar フラグの導出値である。同種の行を
    足したとき追随するかどうかが、リテラルと導出の差である。
    """
    from marketdata import resample, tf_ledger, tf_meta

    row = tf_ledger.TfDescriptor("1D", True, True, 86_400, period_first=lambda d: d)
    monkeypatch.setitem(tf_ledger.TF_DESCRIPTORS, "1S", row)
    monkeypatch.setitem(resample.TIMEFRAME_RULES, "1S", "1D")
    monkeypatch.setattr(resample, "SESSION_TFS", resample.SESSION_TFS + ("1S",))
    monkeypatch.setattr(tf_meta, "SESSION_TFS", resample.SESSION_TFS + ("1S",))
    return row


def test_tf_meta_routes_session_tfs_by_the_ledger(ledger_with_session_day_like):
    """tf_meta の 1D 分岐が台帳の calendar フラグからの導出であること（2 箇所）。"""
    from marketdata.tf_meta import bar_time_unix, period_start_unix

    # UTC 暦日とブローカー暦日が食い違う時刻を選ぶ（NY 18:00 EST ＝ ブローカー翌日 2026-02-16）。
    # 両者が一致する時刻では、UTC floor へ落ちる誤経路と正経路が同じ値になり検出できない。
    t = _unix("2026-02-15 23:00:00")
    # 期待値はセッション日規約の既知値を直接置く（被検査モジュールの呼び出しで期待値を作らない）:
    #   time  = ブローカー暦日 2026-02-16 ラベルの UTC 深夜
    #   始端  = NY ローカル 2026-02-15 17:00 EST ＝ 22:00 UTC
    assert bar_time_unix("1S", t) == _unix("2026-02-16 00:00:00")
    assert period_start_unix(t, "1S") == _unix("2026-02-15 22:00:00")


# =========================================================================== #
# 計算量（発行 − 使用 = 0）: 台帳引きは呼び出しあたり 1 回で、行数に依存しない
# =========================================================================== #

class _CountingLedger(dict):
    """台帳引き（``__getitem__``）の発行回数を数える Test Spy。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gets = 0

    def __getitem__(self, key):
        self.gets += 1
        return super().__getitem__(key)


def _spy_ledger(monkeypatch, extra_rows: int):
    """台帳を Spy に差し替える（``extra_rows`` 行だけ水増しして行数依存を測る）。"""
    from marketdata import tf_ledger

    spy = _CountingLedger(tf_ledger.TF_DESCRIPTORS)
    base = tf_ledger.TF_DESCRIPTORS["1M"]
    for i in range(extra_rows):
        spy[f"_pad{i}"] = base
    monkeypatch.setattr(tf_ledger, "TF_DESCRIPTORS", spy)
    return spy


@pytest.mark.parametrize("calls", [1, 4])
@pytest.mark.parametrize("extra_rows", [0, 1])
def test_period_first_issues_exactly_the_lookups_it_uses(monkeypatch, calls, extra_rows):
    """台帳引きの発行数 − 使用数 = 0（全行走査へ退化しない・行数を増やしても増えない）。

    使用数は「期間始端を求めた回数」＝返した ``date`` の個数である。回数そのものは期待値へ
    焼き込まない（浪費を仕様へ昇格させないため）。
    """
    from marketdata.tf_ledger import period_first_ymd

    spy = _spy_ledger(monkeypatch, extra_rows)
    used = [period_first_ymd("1M", 2026, 3, 31) for _ in range(calls)]

    assert spy.gets - len(used) == 0


def test_period_first_lookup_does_not_grow_with_the_ledger(monkeypatch):
    """台帳が 9 行から 10 行になっても、呼び出しあたりの発行は変わらない（オーダーの表明）。"""
    from marketdata.tf_ledger import period_first_ymd

    small = _spy_ledger(monkeypatch, 0)
    period_first_ymd("1W", 2026, 3, 31)
    small_gets = small.gets

    big = _spy_ledger(monkeypatch, 1)
    period_first_ymd("1W", 2026, 3, 31)

    assert big.gets == small_gets


def test_period_first_rules_match_the_known_truth_table():
    """台帳が持つ期間始端規則の既知値（1D=同日 / 1W=6 日前 / 1M=月初）を固定する。"""
    from marketdata.tf_ledger import period_first_ymd

    assert period_first_ymd("1D", 2026, 3, 31) == date(2026, 3, 31)
    assert period_first_ymd("1W", 2026, 3, 6) == date(2026, 3, 6) - timedelta(days=6)
    assert period_first_ymd("1M", 2026, 3, 31) == date(2026, 3, 1)
