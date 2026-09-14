"""ブローカー時間座標（NY + 7h）の**唯一源**を固定する（ISSUE-502 D-14）。

何が壊れていたか（SOLID 精査 2026-09-06 の実測）:
    座標系を決める 2 つの定数（基準 tz 名・シフト時間数）と、それを使う写像の式が
    ``marketdata/resample.py``（ベクトル面・pandas）と ``marketdata/session_day.py``
    （スカラ面・zoneinfo）の **2 箇所**に書かれていた。片方だけを動かせば、日足・週足・
    月足の境界と ``session_day_start`` が静かに食い違う。出力はどちらも「それらしい」ので、
    値を見ているだけでは気付けない。

本検定が固定するもの:
    1. 定数の唯一源（AST 走査）— tz 名の文字列も ``hours=<数値>`` のリテラルも、
       ``resample.py`` の宣言 1 箇所の外に現れてはならない。
    2. 2 面の一致（characterization）— ベクトル面とスカラ面が、DST 切替を含む広範囲で
       1 点も違わない。是正前の実装との bit 等価はこの 2 面一致で担保される
       （是正前の実測: 前方 20,034 点 / 逆方向 5,844 点でいずれも不一致 0 件）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from marketdata import resample, session_day

_PKG = Path(__file__).resolve().parents[1]

#: 座標系の基準 tz（IANA 名）。**この検定は綴りを 1 箇所だけ持ち、実装から読み直さない**。
_TZ_NAME = "America/New_York"


def _production_modules() -> "list[Path]":
    """``marketdata`` の本番モジュール（テスト・キャッシュを除く全 ``.py``）。"""
    return sorted(
        p for p in _PKG.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    )


def _rel(path: Path) -> str:
    return path.relative_to(_PKG).as_posix()


def _files_declaring_exact_string(text: str) -> "list[str]":
    """文字列定数が **その値ちょうど**で現れるモジュール（docstring 内の言及は含まない）。"""
    hits = []
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value == text
            for node in ast.walk(tree)
        ):
            hits.append(_rel(path))
    return hits


def _files_with_literal_hours_keyword() -> "list[str]":
    """``...(hours=<数値リテラル>)`` を書いているモジュール（シフトの第 2 定義の痕跡）。"""
    hits = []
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "hours" and isinstance(keyword.value, ast.Constant):
                    hits.append(_rel(path))
                    break
    return sorted(set(hits))


# =====================================================================
# A. 定数の唯一源（AST 走査）
# =====================================================================

def test_the_broker_timezone_name_is_spelled_in_exactly_one_module() -> None:
    """基準 tz 名の綴りは ``resample.py`` にしか無い。

    識別力: ``session_day`` へ ``ZoneInfo("America/New_York")`` を書き戻すと Red になる
    （これが D-14 の第 2 定義そのものだった）。
    """
    # Act
    got = _files_declaring_exact_string(_TZ_NAME)

    # Assert
    assert got == ["resample.py"], (
        f"ブローカー時間の基準 tz 名が {got} に現れています。"
        " 座標系の定数は marketdata.resample の 1 箇所だけが持ち、他は import して使ってください。"
    )


def test_the_broker_shift_is_never_written_as_a_bare_hours_literal() -> None:
    """シフト時間数は ``BROKER_SHIFT_HOURS`` からのみ導出される（数値リテラルの再出現を禁ずる）。

    識別力: ``pd.Timedelta(hours=7)`` / ``timedelta(hours=7)`` を書けば、どのモジュールでも
    Red になる（resample 自身も含む＝宣言と使用を分離させる）。
    """
    # Act
    got = _files_with_literal_hours_keyword()

    # Assert
    assert got == [], (
        f"シフト時間数の数値リテラルが {got} に現れています。"
        " marketdata.resample.BROKER_SHIFT_HOURS を参照してください。"
    )


def test_resample_declares_both_coordinate_constants_once() -> None:
    """座標系の 2 定数が ``resample.py`` の module 直下に 1 回ずつ宣言されている。"""
    # Arrange
    tree = ast.parse((_PKG / "resample.py").read_text(encoding="utf-8"))

    # Act
    assigned = [
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    ]

    # Assert
    assert assigned.count("BROKER_TZ_NAME") == 1
    assert assigned.count("BROKER_SHIFT_HOURS") == 1
    assert resample.BROKER_TZ_NAME == _TZ_NAME
    assert resample.BROKER_SHIFT_HOURS == 7


def test_session_day_owns_no_broker_coordinate_constant() -> None:
    """:mod:`marketdata.session_day` は座標系の定数を 1 つも持たない（委譲だけを持つ）。

    識別力: 撤去した旧 private 定数（基準 tz とシフトを保持していた 2 つの束縛）を
    module 直下へ復活させると Red になる。禁じる名前は下の集合が持つ。
    """
    # Arrange / Act
    names = {
        target.id
        for node in ast.parse((_PKG / "session_day.py").read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    # Assert
    assert not (names & {"_NY", "_NY_TZ", "_BROKER_SHIFT", "_BROKER_SHIFT_HOURS"}), (
        f"session_day が座標系の定数を再定義しています: {sorted(names)}"
    )


# =====================================================================
# B. ベクトル面とスカラ面の一致（characterization・bit 等価の担保）
# =====================================================================

#: DST 切替（3 月・11 月）を毎年 2 回跨ぐ 16 年分を 7 時間刻みで走査する。
#: 7 時間刻みにしてあるのは、24 で割り切れない刻みが日内の全時間帯を順に踏むためである。
_SCAN_START = int(pd.Timestamp("2012-01-01", tz="UTC").timestamp())
_SCAN_END = int(pd.Timestamp("2027-12-31", tz="UTC").timestamp())
_SCAN_STEP = 7 * 3600


def test_the_scalar_face_matches_the_vector_face_on_the_forward_mapping() -> None:
    """瞬間 → ブローカー壁時計: スカラ面（``to_broker_time``）とベクトル面が全点一致する。"""
    # Arrange
    seconds = list(range(_SCAN_START, _SCAN_END, _SCAN_STEP))
    index = pd.DatetimeIndex([pd.Timestamp(t, unit="s") for t in seconds])

    # Act
    vector = resample.to_broker_naive_index(index)
    scalar = pd.DatetimeIndex(
        [pd.Timestamp(resample.to_broker_time(t).replace(tzinfo=None)) for t in seconds]
    )

    # Assert
    assert len(seconds) > 20_000, "走査点が痩せたら検出力が落ちる"
    assert int((vector != scalar).sum()) == 0, "2 面が食い違えばセッション境界が 1 時間ずれる"


def test_the_scalar_face_matches_the_vector_face_on_the_inverse_mapping() -> None:
    """ブローカー壁時計 → 瞬間: スカラ面（``from_broker_naive_unix``）とベクトル面が全点一致する。"""
    # Arrange: ブローカー暦日の真夜中（＝セッション始端 NY 17:00）を 16 年分。
    days = pd.date_range("2012-01-01", "2027-12-31", freq="D")

    # Act
    vector = [
        int(pd.Timestamp(v).tz_localize("UTC").timestamp())
        for v in resample.from_broker_naive_index(pd.DatetimeIndex(days))
    ]
    scalar = [
        resample.from_broker_naive_unix(datetime(d.year, d.month, d.day)) for d in days
    ]

    # Assert
    assert len(days) > 5_000
    assert vector == scalar


def test_the_two_faces_round_trip() -> None:
    """順 → 逆が同じ座標系を指す（可逆性の表明）。

    「その瞬間のブローカー暦日の真夜中」を逆写像した値は、必ずその瞬間以下であり、
    翌暦日の真夜中は必ずその瞬間より後になる（＝半開区間 ``[当日, 翌日)`` に収まる）。
    期待値を別の本番関数の戻り値に取らないのは、委譲済みの今それが恒真になるためである。
    """
    # Arrange
    seconds = list(range(_SCAN_START, _SCAN_END, 13 * 3600))

    # Act / Assert
    for t in seconds:
        broker = resample.to_broker_time(t).replace(tzinfo=None)
        midnight = datetime(broker.year, broker.month, broker.day)
        start = resample.from_broker_naive_unix(midnight)
        nxt = resample.from_broker_naive_unix(midnight + timedelta(days=1))
        assert start <= t < nxt


# =====================================================================
# C. 委譲が生きている（session_day が唯一源を使っている）
# =====================================================================

def test_session_day_delegates_the_coordinate_to_resample() -> None:
    """``session_day`` の暦日算出が ``resample`` の実装そのものである（写しでない）。"""
    # Arrange / Act / Assert
    assert session_day._broker_date is resample.to_broker_time


@pytest.mark.parametrize("unix", [
    int(pd.Timestamp("2026-03-08 12:00:00", tz="UTC").timestamp()),   # 米 DST 開始日
    int(pd.Timestamp("2026-11-01 12:00:00", tz="UTC").timestamp()),   # 米 DST 終了日
    int(pd.Timestamp("2026-01-02 21:59:59", tz="UTC").timestamp()),   # 冬の境界直前
    int(pd.Timestamp("2026-01-02 22:00:00", tz="UTC").timestamp()),   # 冬の境界ちょうど
])
def test_session_boundaries_are_unchanged_at_the_hard_cases(unix: int) -> None:
    """DST 切替日・境界ちょうどで、始端が「その時刻を含む半開区間」であり続ける。"""
    # Act
    start = session_day.session_day_start(unix)
    nxt = session_day.next_session_day_start(unix)

    # Assert
    assert start <= unix < nxt
    assert session_day.session_day_start(nxt) == nxt, "境界ちょうどは新セッションに属する"
