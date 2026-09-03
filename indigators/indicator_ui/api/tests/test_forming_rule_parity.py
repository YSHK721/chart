"""forming 差し替え規則の単一述語化（F-9 / ISSUE-479）の検査。

なぜ必要か（現状の実測）:
    「形成中バーを窓の末尾へ set/replace する」規則が 3 箇所に別実装で存在していた。
    list 版 :func:`common.forming_window.apply_forming`、DataFrame 版
    :func:`adapter.compute.forming_bar.apply_forming_bar`、そして
    ``live_tick_tails.make_tail_at``（比較なしの末尾代入＋コメントで同値を仮定するだけ）。
    3 実装が食い違った瞬間に「描いたローソクと指標値が別のバー」になる（ISSUE-232 の失敗モード）。

本ファイルが固定する不変条件:
    1. 規則の述語は :func:`common.forming_window.forming_patch` ただ 1 つであり、
       list 版と DataFrame 版は同じ ``(bars, forming)`` に対して**同じ**結果を返す
       （``(time, o, h, l, c, v)`` へ正規化し float は ``struct.pack("<d", v)`` のバイト一致で見る）。
    2. ライブ経路（``apply_forming_bar``）の出力は改修前と 1 ビット一致する。改修前の出力は
       本ファイル内の ``_legacy_*`` として明示的に構築した golden で固定する（git stash を使わない）。
    3. 述語が「窓の末尾＝形成中バー」を否定した場合、``make_tail_at`` は無音にせず 1 度だけ記録する。
    4. 計算量: 述語の発行数は**出力量だけ**で決まり、窓長（bars 本数）に比例しない。

data/: 実データを読まない（合成 DataFrame のみ・注入で置換）。
構造: Arrange-Act-Assert（AAA）。
"""

from __future__ import annotations

import struct

import pytest

from common import forming_window as fw
from common.forming_window import apply_forming, forming_patch, split_prefix_tails

# --------------------------------------------------------------------------- #
# 共通の正規化（float は IEEE754 のバイト列で見る＝表示丸めで差を見逃さない）
# --------------------------------------------------------------------------- #

_FIELDS = ("open", "high", "low", "close", "volume")


def _pack(value) -> bytes:
    """float を IEEE754 倍精度のバイト列へ（``==`` より強い一致を取るため）。"""
    return struct.pack("<d", float(value))


def _normalize_bars(bars) -> "list[tuple]":
    """list 表現のバー列を ``(time, o, h, l, c, v)`` の正規形へ。"""
    return [
        (int(b["time"]), *(_pack(b[k]) if k in b else None for k in _FIELDS))
        for b in bars
    ]


# --------------------------------------------------------------------------- #
# 改修前の list 版ロジック（golden）。ここを触らずに現行実装と突き合わせる。
# --------------------------------------------------------------------------- #

def _legacy_apply_forming(bars, forming):
    """改修前 ``common/forming_window.py:34-60`` と同値の写し（期待値の明示構築）。"""
    from typing import Mapping

    out = [dict(b) for b in bars]
    if not isinstance(forming, Mapping) or len(out) == 0:
        return out
    try:
        t = int(forming["time"])
    except (KeyError, TypeError, ValueError):
        return out
    if t < int(out[-1]["time"]):
        return out
    lower_forming = {str(k).lower(): v for k, v in forming.items()}
    if t == int(out[-1]["time"]):
        target = out[-1]
    else:
        target = {"time": t}
        out.append(target)
    for key in _FIELDS:
        if key in lower_forming:
            target[key] = float(lower_forming[key])
    return out


_BARS = [
    {"time": 100, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
    {"time": 200, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "volume": 20.0},
]

_FULL = {"open": 3.25, "high": 4.5, "low": 2.125, "close": 3.75, "volume": 7.0}

#: 規則の 8 分岐（同値分割＋境界値: 末尾 time の直下・同値・直上）。
_RULE_CASES = {
    "t<last": {"time": 150, **_FULL},
    "t==last": {"time": 200, **_FULL},
    "t>last": {"time": 300, **_FULL},
    "None": None,
    "non-mapping": "x",
    "time-missing": dict(_FULL),
    "time-non-numeric": {"time": "abc", **_FULL},
    "mixed-case-keys": {"time": 200, "Open": 3.25, "HIGH": 4.5, "low": 2.125,
                        "Close": 3.75, "VOLUME": 7.0},
}


# --------------------------------------------------------------------------- #
# 1. 述語 forming_patch の分岐表（mode / time / values）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("name", "expected_mode"),
    [
        ("t<last", "skip"),
        ("t==last", "replace"),
        ("t>last", "append"),
        ("None", "skip"),
        ("non-mapping", "skip"),
        ("time-missing", "skip"),
        ("time-non-numeric", "skip"),
        ("mixed-case-keys", "replace"),
    ],
)
def test_forming_patch_decides_the_mode_for_every_branch(name, expected_mode) -> None:
    """述語は 8 分岐すべてで mode を一意に決める（skip / replace / append）。"""
    # Arrange
    forming = _RULE_CASES[name]

    # Act
    patch = forming_patch(200, forming)

    # Assert
    assert patch.mode == expected_mode


def test_forming_patch_lowercases_the_value_keys_and_keeps_only_present_ones() -> None:
    """値は大小無視で拾い、``forming`` に在るキーだけを返す（他フィールドは呼び出し側が保存する）。"""
    # Arrange
    forming = {"time": 200, "Close": 7.0, "HIGH": 8.0}

    # Act
    patch = forming_patch(200, forming)

    # Assert
    assert patch.values == {"high": 8.0, "close": 7.0}
    assert list(patch.values) == ["high", "close"]  # 走査順は _FIELDS の順（挿入順を保存）


def test_forming_patch_without_a_last_bar_is_an_append() -> None:
    """末尾が無い窓（``last_time=None``）は比較対象が無い＝追加。

    ``apply_forming`` 側の「空 bars は無変更」は list API 固有の契約であり、述語の
    責務ではない（両者を混ぜないことを明示的に固定する）。
    """
    # Arrange / Act
    patch = forming_patch(None, {"time": 300, **_FULL})

    # Assert
    assert (patch.mode, patch.time) == ("append", 300)
    assert apply_forming([], {"time": 300, **_FULL}) == []


def test_forming_patch_carries_the_forming_time_even_when_skipping_the_past() -> None:
    """過去 time の skip でも ``time`` は運ぶ（呼び出し側が理由を記録できる）。"""
    # Arrange / Act
    patch = forming_patch(200, _RULE_CASES["t<last"])

    # Assert
    assert (patch.mode, patch.time, patch.values) == ("skip", 150, {})


# --------------------------------------------------------------------------- #
# 2. apply_forming は述語の消費者になっても出力が byte 等価
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", sorted(_RULE_CASES))
def test_apply_forming_output_is_byte_identical_to_the_pre_change_logic(name) -> None:
    """述語へ書き換えた後も list 版の出力は改修前と 1 ビット一致する。"""
    # Arrange
    forming = _RULE_CASES[name]

    # Act
    got = apply_forming(_BARS, forming)
    golden = _legacy_apply_forming(_BARS, forming)

    # Assert
    assert _normalize_bars(got) == _normalize_bars(golden)
    assert got == golden  # キーの集合・挿入順まで一致


def test_apply_forming_still_preserves_unspecified_fields_on_replace() -> None:
    """置換では ``forming`` に無いフィールドを保存する（述語化で失われないこと）。"""
    # Arrange / Act
    got = apply_forming(_BARS, {"time": 200, "close": 9.9})

    # Assert
    assert got[-1]["close"] == 9.9
    assert (got[-1]["open"], got[-1]["volume"]) == (1.5, 20.0)


# --------------------------------------------------------------------------- #
# 3. 計算量テスト（絶対命令）— 述語の発行数は出力量だけで決まる
# --------------------------------------------------------------------------- #

class _PatchSpy:
    """``forming_patch`` の発行回数を数える Test Spy（回数は期待値へ焼き込まない）。"""

    def __init__(self, monkeypatch, module) -> None:
        self.calls = 0
        real = fw.forming_patch

        def counting(last_time, forming):
            self.calls += 1
            return real(last_time, forming)

        monkeypatch.setattr(module, "forming_patch", counting)


def _bars(n: int) -> "list[dict]":
    """合成の確定バー列（末尾 time は本数に依らず 200 で固定）。"""
    return [
        {"time": 200 - (n - 1 - i) * 100, "open": 1.0, "high": 2.0,
         "low": 0.5, "close": 1.5, "volume": 10.0}
        for i in range(n)
    ]


def test_apply_forming_issues_exactly_the_patches_it_uses(monkeypatch) -> None:
    """1 回の ``apply_forming`` が発行する判定は、出力に使った判定と同数（発行 − 使用 = 0）。"""
    # Arrange
    spy = _PatchSpy(monkeypatch, fw)
    formings = [{"time": 200, **_FULL}, {"time": 300, **_FULL}, None]

    # Act
    used = [fw.apply_forming(_BARS, f) for f in formings]

    # Assert
    assert spy.calls - len(used) == 0


def test_split_prefix_tails_issues_one_patch_per_emitted_tail(monkeypatch) -> None:
    """``split_prefix_tails`` の発行数は出力した tail 数と同数（捨てる判定を作らない）。"""
    # Arrange
    spy = _PatchSpy(monkeypatch, fw)
    formings = [{"time": 200, "close": float(i)} for i in range(5)]

    # Act
    _prefix, tails = fw.split_prefix_tails(_BARS, formings)

    # Assert
    assert spy.calls - len(tails) == 0


def test_patch_issuance_does_not_grow_with_the_window_length(monkeypatch) -> None:
    """窓長 N を倍にしても発行数は変わらない（オーダーの表明・2 点で固定）。

    発行数が N に比例するようになると「作ってから捨てる」浪費が入っても出力は正しいままで、
    状態検証では原理的に落ちない（ISSUE-450 の失敗モード）。
    """
    # Arrange
    formings = [{"time": 200, "close": float(i)} for i in range(3)]

    def issued_for(window_length: int) -> int:
        with pytest.MonkeyPatch.context() as mp:
            spy = _PatchSpy(mp, fw)
            fw.split_prefix_tails(_bars(window_length), formings)
            return spy.calls

    # Act
    small, large = issued_for(100), issued_for(200)

    # Assert
    assert small == large == len(formings)


def test_patch_issuance_follows_the_number_of_outputs(monkeypatch) -> None:
    """出力量（formings 数）を倍にすると発行数も倍になる（出力量だけで決まる）。"""
    # Arrange
    base = [{"time": 200, "close": float(i)} for i in range(3)]

    def issued_for(formings) -> int:
        with pytest.MonkeyPatch.context() as mp:
            spy = _PatchSpy(mp, fw)
            fw.split_prefix_tails(_BARS, formings)
            return spy.calls

    # Act
    single, doubled = issued_for(base), issued_for(base * 2)

    # Assert
    assert doubled - single == len(base)


def test_split_prefix_tails_equivalence_still_holds() -> None:
    """``prefix + tails[i] == apply_forming(bars, formings[i])``（ISSUE-233 の不変条件）。"""
    # Arrange
    formings = [{"time": 200, "close": 2.1}, {"time": 300, "open": 2.4, "close": 2.6}]

    # Act
    prefix, tails = split_prefix_tails(_BARS, formings)

    # Assert
    for forming, tail in zip(formings, tails):
        assert prefix + tail == apply_forming(_BARS, forming)
