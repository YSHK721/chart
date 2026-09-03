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

#: 末尾バーに ``time`` が無い異常窓（旧実装の「末尾をいつ見るか」を固定するための材料）。
_BARS_WITHOUT_TAIL_TIME = [
    {"time": 100, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
    {"open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "volume": 20.0},
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


#: 末尾 time を**見ずに**無変更で終わる入力（forming 側だけで判定が確定する）。
_TAIL_TIME_NOT_REQUIRED = ("None", "non-mapping", "time-missing", "time-non-numeric")

#: 末尾 time との比較が要る入力（末尾 time の欠落はここで初めて露出する）。
_TAIL_TIME_REQUIRED = ("t<last", "t==last", "t>last", "mixed-case-keys")


@pytest.mark.parametrize("name", _TAIL_TIME_NOT_REQUIRED)
def test_an_invalid_forming_is_decided_without_looking_at_the_window_tail(name) -> None:
    """``forming`` 側だけで無変更が確定する入力は、窓の末尾を参照せずに終わる（旧実装の順序）。

    旧実装は「非 Mapping・``time`` 欠落/非数値なら末尾を見ずに無変更」の順序を持っていた。
    述語化でこの順序が失われると、無変更で済むはずの入力（``forming=None`` 等）が末尾バーの
    ``time`` 欠落で ``KeyError`` になる。緩和ではなく硬化だが、どちらも「挙動を 1 ビットも
    変えない」に反する（F-9 の適用範囲は規則の単一化であって挙動の変更ではない）。
    """
    # Arrange
    forming = _RULE_CASES[name]

    # Act
    got = apply_forming(_BARS_WITHOUT_TAIL_TIME, forming)

    # Assert
    assert got == _legacy_apply_forming(_BARS_WITHOUT_TAIL_TIME, forming)


@pytest.mark.parametrize("name", _TAIL_TIME_REQUIRED)
def test_a_valid_forming_still_requires_the_window_tail_time(name) -> None:
    """末尾との比較が要る入力では、末尾 ``time`` の欠落を握らず旧実装と同じ例外を出す。"""
    # Arrange
    forming = _RULE_CASES[name]
    with pytest.raises(KeyError):
        _legacy_apply_forming(_BARS_WITHOUT_TAIL_TIME, forming)  # 旧実装の露出（期待値の明示）

    # Act / Assert
    with pytest.raises(KeyError):
        apply_forming(_BARS_WITHOUT_TAIL_TIME, forming)


def test_a_null_tail_time_is_not_silently_treated_as_an_empty_window() -> None:
    """末尾 ``time`` が ``None`` の窓は、比較不能として例外になる（黙って追加しない）。

    実測（3 版の突合）: 改修前は ``int(None)`` の ``TypeError``、述語化の途中版は
    「末尾が無い窓」とみなして**黙って足を 1 本追加**していた。追加された足は確定バーの
    直後に別 time で並ぶため、描画と指標が食い違う（ISSUE-232 の失敗モード）。比較材料が
    壊れているときに黙って別の答えを出さない、が規則の側の要求である。
    """
    # Arrange
    bars = [{"time": 100, "close": 1.0}, {"time": None, "close": 2.0}]

    # Act / Assert
    with pytest.raises(TypeError):
        _legacy_apply_forming(bars, {"time": 200, "close": 9.0})   # 改修前の露出（期待値）
    with pytest.raises(TypeError):
        apply_forming(bars, {"time": 200, "close": 9.0})


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


# --------------------------------------------------------------------------- #
# 4. list 版 / DataFrame 版の突合（同じ規則が同じ答えを出す）
# --------------------------------------------------------------------------- #

import pandas as pd  # noqa: E402  — 突合は DataFrame 版が要るため、ここから下でのみ使う
from pandas.testing import assert_frame_equal  # noqa: E402

from adapter.compute import forming_bar as fb  # noqa: E402

_REF = "jp225_tick"
_TF = "5m"
_NOW = 999


def _unix(text: str) -> int:
    """naive UTC の日時文字列 → UNIX 秒（``.timestamp()`` は使わない）。"""
    return int(pd.Timestamp(text).value // 1_000_000_000)


def _frame(bars, *, columns=None) -> "pd.DataFrame":
    """list 表現のバー列を date-index の DataFrame へ射影する（同じ材料の別表現）。"""
    index = pd.DatetimeIndex(
        [pd.Timestamp(int(b["time"]), unit="s") for b in bars], name="date"
    )
    frame = pd.DataFrame({k: [float(b[k]) for b in bars] for k in _FIELDS}, index=index)
    if columns is not None:
        frame.columns = columns
    return frame


def _normalize_frame(frame) -> "list[tuple]":
    """DataFrame 表現を list 表現と同じ正規形 ``(time, o, h, l, c, v)`` へ。"""
    lower = {str(c).lower(): c for c in frame.columns}
    return [
        (
            int(pd.Timestamp(ts).value // 1_000_000_000),
            *(_pack(row[lower[k]]) if k in lower else None for k in _FIELDS),
        )
        for ts, row in frame.iterrows()
    ]


def _forming_at(text: str, values: "dict") -> "dict":
    """``text`` 時刻の形成中バー（DataFrame 版の事前条件を満たす 5 キー完備）。"""
    return {"time": _unix(text), **values}


#: 突合用の窓（末尾 = 09:05）。list / DataFrame の同じ材料。
_WINDOW = [
    {"time": _unix("2025-01-02 09:00:00"), "open": 1.0, "high": 2.0,
     "low": 0.5, "close": 1.5, "volume": 10.0},
    {"time": _unix("2025-01-02 09:05:00"), "open": 1.5, "high": 2.5,
     "low": 1.0, "close": 2.0, "volume": 20.0},
]

#: 8 分岐を DataFrame 版が受け取れる形（5 キー完備）で表現したもの。
_PARITY_CASES = {
    "t<last": _forming_at("2025-01-02 09:00:00", _FULL),
    "t==last": _forming_at("2025-01-02 09:05:00", _FULL),
    "t>last": _forming_at("2025-01-02 09:10:00", _FULL),
    "None": None,
    "non-mapping": "x",
    "time-missing": dict(_FULL),
    "time-non-numeric": {"time": "abc", **_FULL},
    "mixed-case-keys": {"time": _unix("2025-01-02 09:05:00"), "Open": 3.25,
                        "HIGH": 4.5, "low": 2.125, "Close": 3.75, "VOLUME": 7.0},
}


def test_the_dataframe_path_derives_the_rule_from_the_shared_predicate() -> None:
    """DataFrame 版は規則を持たず、共有核の述語を参照する（実装の二重化を構造で禁じる）。"""
    # Arrange / Act / Assert
    assert fb.forming_patch is fw.forming_patch


#: DataFrame 版が **例外で拒む** 入力と、その種別（adapter 固有の事前条件・下記テスト参照）。
_DATAFRAME_FAIL_STOP = {
    "time-missing": KeyError,
    "time-non-numeric": ValueError,
    "non-mapping": TypeError,
}

#: 両版の出力が一致する分岐（＝事前条件を満たす入力）。
_AGREEING_CASES = tuple(sorted(set(_PARITY_CASES) - set(_DATAFRAME_FAIL_STOP)))


@pytest.mark.parametrize("name", _AGREEING_CASES)
def test_list_and_dataframe_paths_agree_on_every_branch(name, monkeypatch) -> None:
    """同じ ``(bars, forming)`` に対し list 版と DataFrame 版の出力が byte 一致する。"""
    # Arrange
    forming = _PARITY_CASES[name]
    frame = _frame(_WINDOW)
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: forming)

    # Act
    from_list = apply_forming(_WINDOW, forming)
    from_frame = fb.apply_forming_bar(
        frame, _REF, _TF, _NOW, synthesize_closed_gaps=False
    )

    # Assert
    assert _normalize_frame(from_frame) == _normalize_bars(from_list)


@pytest.mark.parametrize(("name", "expected"), sorted(_DATAFRAME_FAIL_STOP.items()))
def test_a_malformed_forming_bar_keeps_its_branching(name, expected, monkeypatch) -> None:
    """壊れた形成中バーは分岐を保存する: list は skip（無変更）・DataFrame は例外で止まる。

    共有核は「非 Mapping・``time`` 欠落/非数値」を ``"skip"`` へ丸める（list 版の防御）。一方
    ライブ注入が作るのは常に OHLCV 完備・``time`` 付きのバーなので、DataFrame 版にとって
    これらは**上流の破損**である。丸めた結果を素通しにすると「注入しなかった」と区別が
    つかなくなり、最新足だけ指標が消える障害が痕跡なく起きる。例外種別まで固定するのは、
    F-9（規則の単一化）が挙動を 1 ビットも変えていないことの証拠にするため
    （旧実装 ``pd.Timestamp(int(bar["time"]), unit="s")`` が送出していた種別と同一）。
    """
    # Arrange
    forming = _PARITY_CASES[name]
    frame = _frame(_WINDOW)
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: forming)

    # Act
    from_list = apply_forming(_WINDOW, forming)

    # Assert
    assert from_list == _legacy_apply_forming(_WINDOW, forming)   # list 版は無変更のまま
    with pytest.raises(expected):
        fb.apply_forming_bar(frame, _REF, _TF, _NOW, synthesize_closed_gaps=False)


def test_missing_ohlcv_keys_keep_their_branching(monkeypatch) -> None:
    """5 キー欠落は分岐を保存する: list は skip（在るキーだけ更新）・DataFrame は KeyError。

    共有核は「forming に在るキーだけ更新」で欠落を許すが、ライブ注入は OHLCV 完備のバーしか
    作らない。欠落は上流の破損なので DataFrame 版は握らず露出させる（事前条件を緩めない）。
    """
    # Arrange
    partial = {"time": _unix("2025-01-02 09:05:00"), "close": 9.9}
    frame = _frame(_WINDOW)
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: partial)

    # Act
    from_list = apply_forming(_WINDOW, partial)

    # Assert
    assert from_list[-1]["close"] == 9.9 and from_list[-1]["open"] == 1.5
    with pytest.raises(KeyError):
        fb.apply_forming_bar(frame, _REF, _TF, _NOW, synthesize_closed_gaps=False)


def test_missing_key_without_a_matching_column_is_not_a_precondition_breach(monkeypatch) -> None:
    """列が無いキーの欠落は従来どおり無害（事前条件は「列が在るキー」だけに掛かる）。"""
    # Arrange
    frame = _frame(_WINDOW)[["close"]]
    partial = {"time": _unix("2025-01-02 09:05:00"), "close": 9.9}
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: partial)

    # Act
    out = fb.apply_forming_bar(frame, _REF, _TF, _NOW, synthesize_closed_gaps=False)

    # Assert
    assert float(out.iloc[-1]["close"]) == 9.9 and len(out) == len(_WINDOW)


# --------------------------------------------------------------------------- #
# 5. ライブ経路の 1 ビット不変（改修前ロジックの golden をテスト内で明示構築）
# --------------------------------------------------------------------------- #

def _legacy_inject(df, to_inject):
    """改修前 ``forming_bar.py:326-334`` と同値の写し（期待値の明示構築）。"""
    out = df.copy()
    lower = {str(c).lower(): c for c in out.columns}
    for b in to_inject:
        bt = pd.Timestamp(int(b["time"]), unit="s")
        for key in _FIELDS:
            col = lower.get(key)
            if col is not None:
                out.loc[bt, col] = float(b[key])
    return out.sort_index()


@pytest.mark.parametrize("name", ["t==last", "t>last"])
def test_live_injection_output_is_bit_identical_to_the_pre_change_logic(name, monkeypatch) -> None:
    """注入結果が改修前と 1 ビット一致する（``check_exact=True``）。"""
    # Arrange
    forming = _PARITY_CASES[name]
    frame = _frame(_WINDOW)
    golden = _legacy_inject(frame, [forming])
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: forming)

    # Act
    out = fb.apply_forming_bar(frame, _REF, _TF, _NOW, synthesize_closed_gaps=False)

    # Assert
    assert_frame_equal(out, golden, check_exact=True)


def test_live_injection_with_uppercase_columns_is_bit_identical(monkeypatch) -> None:
    """大文字列名の窓でも改修前と 1 ビット一致する（列照合は大小無視のまま）。"""
    # Arrange
    forming = _PARITY_CASES["t>last"]
    frame = _frame(_WINDOW, columns=[c.upper() for c in _FIELDS])
    golden = _legacy_inject(frame, [forming])
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: forming)

    # Act
    out = fb.apply_forming_bar(frame, _REF, _TF, _NOW, synthesize_closed_gaps=False)

    # Assert
    assert_frame_equal(out, golden, check_exact=True)


def test_synthesized_closed_gap_injection_is_bit_identical(monkeypatch) -> None:
    """欠落閉周期の合成を伴う経路（ISSUE-162）も改修前と 1 ビット一致する。"""
    # Arrange
    frame = _frame(_WINDOW[:1])
    forming = _forming_at("2025-01-02 09:02:00", _FULL)
    closed = _forming_at("2025-01-02 09:01:00",
                         {"open": 2.0, "high": 2.5, "low": 1.9, "close": 2.2, "volume": 6.0})
    golden = _legacy_inject(frame, [closed, forming])
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: forming)
    monkeypatch.setattr(
        fb, "forming_bar_from_ticks",
        lambda s, e: closed if s == closed["time"] else None,
    )

    # Act
    out = fb.apply_forming_bar(frame, _REF, "1m", _NOW)

    # Assert
    assert_frame_equal(out, golden, check_exact=True)


def test_no_injection_still_passes_the_same_object_through(monkeypatch) -> None:
    """注入するものが無ければ複製もせず同一オブジェクトを返す（:324 の素通し契約）。"""
    # Arrange
    frame = _frame(_WINDOW)
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: None)

    # Act
    out = fb.apply_forming_bar(frame, _REF, _TF, _NOW, synthesize_closed_gaps=False)

    # Assert
    assert out is frame


def test_a_forming_bar_older_than_the_window_tail_passes_the_frame_through(monkeypatch) -> None:
    """末尾より過去の形成中バーは触らない（防御分岐が述語由来になっても同一オブジェクト）。"""
    # Arrange
    frame = _frame(_WINDOW)
    monkeypatch.setattr(fb, "forming_bar", lambda *a, **k: _PARITY_CASES["t<last"])

    # Act
    out = fb.apply_forming_bar(frame, _REF, _TF, _NOW, synthesize_closed_gaps=False)

    # Assert
    assert out is frame


# --------------------------------------------------------------------------- #
# 6. ライブ末尾値（make_tail_at）— 述語で "replace" を実確認し、否定を無音にしない
# --------------------------------------------------------------------------- #

import logging  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from adapter.compute import live_tick_tails as ltt  # noqa: E402
from adapter.controller.live_tick_tails_controller import _set_last_bar  # noqa: E402

_LTT_LOGGER = "adapter.compute.live_tick_tails"


def _spec():
    """``tail_at`` が要する最小の spec（指標の同定だけ）。"""
    return SimpleNamespace(indicator_id="x", variant=None, params={})


def _state(text: str):
    """``text`` 時刻のバーに属する形成中バーの累積状態。"""
    return SimpleNamespace(
        time=_unix(text), open=1.0, high=2.0, low=0.5, close=1.75, volume=3,
        tick_ms=_unix(text) * 1000,
    )


def _tail_at_over(window, monkeypatch):
    """実 ``make_tail_at`` を最小の協調子で組み立てる（増分宣言と計算は注入で置換）。"""
    monkeypatch.setattr(ltt, "is_incremental", lambda *a, **k: True)
    return ltt.make_tail_at(
        df=window,
        adapter=object(),
        latest_compute=lambda *a, **k: [{"name": "v", "data": [{"value": 1.0}]}],
        set_last_bar=_set_last_bar,
    )


def test_the_live_tail_path_derives_the_rule_from_the_shared_predicate() -> None:
    """ライブ末尾値の経路も規則を持たず、共有核の述語を参照する。"""
    # Arrange / Act / Assert
    assert ltt.forming_patch is fw.forming_patch


def test_a_mismatched_window_is_recorded_once_and_still_yields_tails(monkeypatch, caplog) -> None:
    """末尾 time と形成中バーの周期が食い違う窓は、1 度だけ記録して tails を落とさない。

    従来は比較なしで末尾行へ代入し、「窓の末尾＝形成中バーと同じバー」をコメントで仮定して
    いただけだった。仮定が破れると別のバーの値を黙って描く（ISSUE-232 の失敗モード）。
    """
    # Arrange — 窓の末尾は 09:00、形成中バーは 09:05（＝別のバー）。
    window = _frame(_WINDOW[:1])
    tail_at = _tail_at_over(window, monkeypatch)

    # Act
    with caplog.at_level(logging.WARNING, logger=_LTT_LOGGER):
        first = tail_at(_spec(), _state("2025-01-02 09:05:00"))
        second = tail_at(_spec(), _state("2025-01-02 09:05:00"))

    # Assert
    records = [r for r in caplog.records if r.name == _LTT_LOGGER]
    assert len(records) == 1                      # 無音にしない・かつ毎ティック吐かない
    assert first == {"v": 1.0} and second == {"v": 1.0}   # tails は落ちない


def test_a_matching_window_is_not_recorded(monkeypatch, caplog) -> None:
    """窓の末尾と形成中バーが同じバー（mode == "replace"）なら記録しない。"""
    # Arrange — 窓の末尾 09:05 と形成中バー 09:05 が一致。
    window = _frame(_WINDOW)
    tail_at = _tail_at_over(window, monkeypatch)

    # Act
    with caplog.at_level(logging.WARNING, logger=_LTT_LOGGER):
        got = tail_at(_spec(), _state("2025-01-02 09:05:00"))

    # Assert
    assert [r for r in caplog.records if r.name == _LTT_LOGGER] == []
    assert got == {"v": 1.0}


def test_the_tail_row_assignment_is_unchanged(monkeypatch) -> None:
    """末尾行へ渡す値は従来どおり OHLCV の 5 キーのみ（``time`` は渡さない＝列照合は完全一致）。"""
    # Arrange
    window = _frame(_WINDOW)
    seen: "list[dict]" = []
    monkeypatch.setattr(ltt, "is_incremental", lambda *a, **k: True)
    tail_at = ltt.make_tail_at(
        df=window,
        adapter=object(),
        latest_compute=lambda *a, **k: [{"name": "v", "data": [{"value": 1.0}]}],
        set_last_bar=lambda w, values: seen.append(dict(values)),
    )

    # Act
    tail_at(_spec(), _state("2025-01-02 09:05:00"))

    # Assert
    assert seen == [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.75, "volume": 3.0}]
    assert float(window.iloc[-1]["close"]) == 2.0  # 入力 df は複製されており不変
