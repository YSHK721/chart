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
    3. 述語が ``"skip"``（形成中バーが窓末尾より過去＝順序逆転）を返した場合、
       ``make_tail_at`` は無音にせず 1 度だけ記録する（``"append"``＝バーが進んだ、は
       ISSUE-481 で窓へ行を足して吸収するため食い違いではない）。
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
        inject=fb.inject_forming_bars,
    )


def test_the_live_tail_path_derives_the_rule_from_the_shared_predicate() -> None:
    """ライブ末尾値の経路も規則を持たず、共有核の述語を参照する。"""
    # Arrange / Act / Assert
    assert ltt.forming_patch is fw.forming_patch


def test_a_mismatched_window_is_recorded_once_and_still_yields_tails(monkeypatch, caplog) -> None:
    """末尾 time と形成中バーの周期が食い違う窓は、1 度だけ記録して tails を落とさない。

    従来は比較なしで末尾行へ代入し、「窓の末尾＝形成中バーと同じバー」をコメントで仮定して
    いただけだった。仮定が破れると別のバーの値を黙って描く（ISSUE-232 の失敗モード）。

    ISSUE-481: 「形成中バーが窓末尾より**新しい**」は食い違いではなく「バーが進んだ」なので、
    窓へ行を足して吸収する（警告は出ない）。本当に説明のつかない入力は残る ``"skip"``
    ＝形成中バーが窓末尾より過去（順序が逆転した tick）だけであり、警告はそこ専用の信号に
    なった。したがって Arrange をその入力へ移す（assert する性質＝無音にしない・毎ティック
    吐かない・tails を落とさない、は不変）。
    """
    # Arrange — 窓の末尾は 09:05、形成中バーは 09:00（＝末尾より過去＝順序逆転）。
    window = _frame(_WINDOW)
    tail_at = _tail_at_over(window, monkeypatch)

    # Act
    with caplog.at_level(logging.WARNING, logger=_LTT_LOGGER):
        first = tail_at(_spec(), _state("2025-01-02 09:00:00"))
        second = tail_at(_spec(), _state("2025-01-02 09:00:00"))

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


def test_a_window_without_a_clock_index_never_grows_a_row(monkeypatch, caplog) -> None:
    """時刻 index でない窓へは行を足さない（比較材料が無いまま新しいバーと決めない）。

    ``window_with_forming`` は末尾 time を読めない窓を素通しし、「呼び出し側が記録する」
    契約にしている（末尾 time の解決が None になる）。``make_tail_at`` が行追加をこの窓へも
    適用すると、整数 index へ時刻ラベルの行が混ざって並べ替えが ``TypeError`` で落ちる
    （実測: `/live_ticks` の既存検定 19 件が同時に落ちた）。行を足せるのは「窓末尾より
    新しい」と**確かめられた**ときだけである。
    """
    # Arrange — index が時刻でない窓（時刻は列として持つ形）。
    window = pd.DataFrame({
        "time": [100, 200], "open": [1.0, 1.5], "high": [2.0, 2.5],
        "low": [0.5, 1.0], "close": [1.5, 2.0], "volume": [10.0, 20.0],
    })
    tail_at = _tail_at_over(window, monkeypatch)

    # Act
    with caplog.at_level(logging.WARNING, logger=_LTT_LOGGER):
        got = tail_at(_spec(), _state("2025-01-02 09:05:00"))

    # Assert — 行は増えず、対応不明として 1 度だけ記録される。
    assert got == {"v": 1.0}
    assert len([r for r in caplog.records if r.name == _LTT_LOGGER]) == 1


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
        inject=fb.inject_forming_bars,
    )

    # Act
    tail_at(_spec(), _state("2025-01-02 09:05:00"))

    # Assert
    assert seen == [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.75, "volume": 3.0}]
    assert float(window.iloc[-1]["close"]) == 2.0  # 入力 df は複製されており不変


# --------------------------------------------------------------------------- #
# 7. 計算量テスト（絶対命令）— 閉周期合成は gap 長を実体化せず上限で有界
# --------------------------------------------------------------------------- #

class _SynthesisSpy:
    """素材集計（forming_bar_from_ticks）が受け取った窓 ``(start, end)`` を記録する Test Spy。

    回数そのものは期待値へ焼き込まない（焼き込むと浪費が仕様へ昇格する＝ISSUE-450）。
    固定するのは **無駄の不在**（gap 長を増やしても発行が増えないこと）だけである。
    """

    def __init__(self, monkeypatch, *, result=None) -> None:
        self.windows: "list[tuple[int, int]]" = []

        def counting(start, end):
            self.windows.append((int(start), int(end)))
            return None if result is None else result(int(start), int(end))

        monkeypatch.setattr(fb, "forming_bar_from_ticks", counting)


def _issued_for_gap(gap_periods: int) -> "list[tuple[int, int]]":
    """欠落 ``gap_periods`` 周期に対して閉周期合成が発行した窓の列を返す。"""
    period = fb.fixed_period_seconds(_REF, "1m")
    last = _unix("2025-01-02 09:00:00")
    with pytest.MonkeyPatch.context() as mp:
        spy = _SynthesisSpy(mp)
        fb.closed_gap_bars(_REF, "1m", last, last + period * (gap_periods + 1))
        return spy.windows


def test_closed_gap_synthesis_does_not_grow_with_the_gap_length() -> None:
    """欠落 gap を 10 倍にしても合成の発行数は変わらない（オーダーの表明・2 点で固定）。

    ここが gap 長に比例すると、出力（窓に並ぶ行）は正しいまま「作っては捨てる」浪費が入り、
    状態検証では原理的に落ちない（ISSUE-450 の失敗モード）。上限は実装の定数から導出し、
    回数リテラルは書かない。
    """
    # Arrange / Act
    small, large = _issued_for_gap(2000), _issued_for_gap(20000)

    # Assert
    assert small, "合成が 1 度も発行されていません（検定が空振りしています）"
    assert len(small) == len(large)                     # gap 長に非比例
    assert len(large) <= fb._MAX_GAP_FILL_PERIODS       # 上限で有界


def test_the_gap_enumeration_is_not_materialised() -> None:
    """欠落周期の列挙は ``range`` のまま扱う（gap 長ぶんの list を作って捨てない）。

    ``list(...)`` へ落とすと、上限で切り詰める前に gap 長ぶんの要素を必ず実体化する。
    出力は同じなので状態検証では見えない浪費であり、型そのものを固定して禁じる。
    """
    # Arrange / Act
    starts = fb._gap_starts(0, 60 * 20000, 60)

    # Assert
    assert isinstance(starts, range)


# --------------------------------------------------------------------------- #
# 8. 2 供給経路の窓同値（ISSUE-481）— 同じ穴を同じ規則で埋める
#
#    /compute（apply_forming_bar）と /live_ticks の窓供給（window_with_forming）は、
#    M1 焼き込み猶予中に開く**同じ**穴を埋めなければならない。片方だけが埋めていた間、
#    2 経路は別の窓で計算しており、実データ 24 点中 5-6 点で無音のまま食い違っていた
#    （ISSUE-481 の実測）。ここで固定するのは「両者が同じ答えを出す」ことであって、
#    どちらか一方の実装詳細ではない。
# --------------------------------------------------------------------------- #

#: 突合の行列: 計算足 × 「確定末尾から形成中周期までの周期数 k」。
#:   k=1 は穴なし・k=2,3 は穴あり・k=6 は上限ちょうど・k=8 は上限超過（縮退）。
_PARITY_TFS = ("1m", "5m")
_PARITY_OFFSETS = (1, 2, 3, 6, 8)

#: 突合の基準となる確定末尾の時刻。
_PARITY_LAST = "2025-01-02 09:00:00"


def _closed_at(start: int) -> "dict":
    """始端 ``start`` の合成閉周期バー（値を始端から一意に導き、取り違えを検出可能にする）。"""
    base = float(start % 100000)
    return {"time": int(start), "open": base, "high": base + 2.0,
            "low": base - 1.0, "close": base + 1.0, "volume": 5.0}


def _parity_setup(tf: str, k: int) -> "tuple":
    """``(確定窓, 形成中バー, 周期秒)`` — 確定末尾から ``k`` 周期先が形成中周期。"""
    period = fb.fixed_period_seconds(_REF, tf)
    last = _unix(_PARITY_LAST)
    frame = _frame([
        {"time": last - period, "open": 1.0, "high": 2.0,
         "low": 0.5, "close": 1.5, "volume": 10.0},
        {"time": last, "open": 1.5, "high": 2.5,
         "low": 1.0, "close": 2.0, "volume": 20.0},
    ])
    bar = {"time": last + period * k, **_FULL}
    return frame, bar, period


def _label_gaps(frame) -> "list[int]":
    """窓のラベル間隔（秒）の列。穴が残っていれば周期の整数倍として現れる。"""
    labels = [int(pd.Timestamp(ts).value // 1_000_000_000) for ts in frame.index]
    return [b - a for a, b in zip(labels, labels[1:])]


def _run_both(tf: str, k: int, *, reader=None) -> "tuple":
    """両経路を**同じ材料**で走らせ、``((窓, 合成呼び出し列), (窓, 合成呼び出し列))`` を返す。

    合成素材の読み手（``reader``）は両側で同一のものを注入する。したがって残る差は
    「規則の実装」だけになり、突合はそこだけを見る。
    """
    frame, bar, _period = _parity_setup(tf, k)
    reader = reader or (lambda s, e: _closed_at(s))

    with pytest.MonkeyPatch.context() as mp:
        spy = _SynthesisSpy(mp, result=reader)
        mp.setattr(fb, "forming_bar", lambda *a, **kw: bar)
        from_compute = fb.apply_forming_bar(
            frame, _REF, tf, _NOW, synthesize_closed_gaps=True
        )
        compute_calls = list(spy.windows)

    with pytest.MonkeyPatch.context() as mp:
        spy = _SynthesisSpy(mp, result=reader)
        from_tails = ltt.window_with_forming(
            frame, bar, inject=fb.inject_forming_bars,
            gap_bars=lambda last: fb.closed_gap_bars(_REF, tf, last, int(bar["time"])),
        )
        tails_calls = list(spy.windows)

    return (from_compute, compute_calls), (from_tails, tails_calls)


@pytest.mark.parametrize("k", _PARITY_OFFSETS)
@pytest.mark.parametrize("tf", _PARITY_TFS)
def test_the_two_supply_paths_produce_the_same_window(tf, k) -> None:
    """P-1 窓同値: 2 経路の窓は index も値も byte 一致する（穴の有無・上限超過を含む）。"""
    # Arrange / Act
    (from_compute, _), (from_tails, _) = _run_both(tf, k)

    # Assert
    assert _normalize_frame(from_tails) == _normalize_frame(from_compute)


@pytest.mark.parametrize("k", _PARITY_OFFSETS)
@pytest.mark.parametrize("tf", _PARITY_TFS)
def test_the_two_supply_paths_issue_the_same_synthesis_windows(tf, k) -> None:
    """P-2 合成呼び出し列同一: 素材へ要求した窓 ``(start, end)`` の列が 2 経路で同じ。

    窓が一致していても要求の出し方が違えば、片方だけが余分に読む（＝作って捨てる）か、
    片方だけが読み落とす。出力の一致だけでは原理的に見えないので発行側も突き合わせる。
    """
    # Arrange / Act
    (_, compute_calls), (_, tails_calls) = _run_both(tf, k)

    # Assert — 発行は「穴の数（上限で頭打ち）」ちょうど＝作って捨てる要求がゼロ。
    assert tails_calls == compute_calls
    assert len(compute_calls) == min(max(k - 1, 0), fb._MAX_GAP_FILL_PERIODS)


def test_the_two_supply_paths_degrade_identically_over_the_cap() -> None:
    """P-3a 縮退同値: 上限超過でも 2 経路は同じ本数だけ充填し、残る穴も同じ位置に残る。"""
    # Arrange / Act
    (from_compute, compute_calls), (from_tails, tails_calls) = _run_both("1m", 8)

    # Assert
    assert _normalize_frame(from_tails) == _normalize_frame(from_compute)
    assert len(tails_calls) == len(compute_calls) == fb._MAX_GAP_FILL_PERIODS
    assert _label_gaps(from_tails) == _label_gaps(from_compute)
    assert max(_label_gaps(from_tails)) > 60, "上限超過なのに穴が残っていません（前提崩れ）"


def test_the_two_supply_paths_add_nothing_when_the_periods_have_no_ticks() -> None:
    """P-3b 縮退同値: tick の無い周期（週末等）では 2 経路とも合成行を足さない。"""
    # Arrange / Act
    (from_compute, _), (from_tails, _) = _run_both("1m", 3, reader=lambda s, e: None)

    # Assert
    assert _normalize_frame(from_tails) == _normalize_frame(from_compute)
    assert _label_gaps(from_tails) == [60, 180]   # 確定 2 本 + 形成中のみ（合成なし）


def test_the_two_supply_paths_skip_only_the_failing_period() -> None:
    """P-3c 縮退同値: 素材読込が失敗した周期だけを 2 経路とも飛ばす（他は合成する）。"""
    # Arrange
    _frame_in, _bar, period = _parity_setup("1m", 3)
    boom = _unix(_PARITY_LAST) + period          # 最初の欠落周期だけ失敗させる

    def reader(s, e):
        if s == boom:
            raise OSError("torn read")
        return _closed_at(s)

    # Act
    (from_compute, compute_calls), (from_tails, tails_calls) = _run_both(
        "1m", 3, reader=reader
    )

    # Assert
    assert _normalize_frame(from_tails) == _normalize_frame(from_compute)
    assert tails_calls == compute_calls           # 失敗した周期も両者が同じだけ要求する
    assert len(from_tails) == len(_frame_in) + 2  # 合成 1 本（1 本は失敗）＋形成中 1 本


def test_the_supplied_window_keeps_the_material_identity() -> None:
    """P-5 素材識別保存: 供給後の窓の素材識別が入力と一致する（状態キーを壊さない）。

    識別（ISSUE-465）が落ちると増分計算の状態キャッシュが素材を区別できなくなり、
    足を巡回するたび全再構築が起きる（実測 0.2ms → 374ms）。値は変わらないので
    状態検証では落ちない種類の劣化であり、識別そのものを固定する。
    """
    # Arrange
    from marketdata.material_identity import label, material_of

    frame, bar, _period = _parity_setup("1m", 3)
    label(frame, ref=_REF, timeframe="1m")
    expected = material_of(frame)

    # Act
    with pytest.MonkeyPatch.context() as mp:
        _SynthesisSpy(mp, result=lambda s, e: _closed_at(s))
        out = ltt.window_with_forming(
            frame, bar, inject=fb.inject_forming_bars,
            gap_bars=lambda last: fb.closed_gap_bars(_REF, "1m", last, int(bar["time"])),
        )

    # Assert
    assert expected is not None                  # 前提（識別が載っている）
    assert material_of(out) == expected


# --------------------------------------------------------------------------- #
# 9. P-4 検出力（負の対照）— 突合が「規則の書き直し」を実際に捕まえる
# --------------------------------------------------------------------------- #

def _mutant_gap_bars(tf: str, bar: "dict", *, cap=None, start_offset: int = 1):
    """供給側が閉周期合成の規則を**自前で書き直した**場合の突然変異。

    ISSUE-481 で避けたいのはまさにこれ（上限の再宣言・列挙起点の取り違え）である。
    突合がこれを捕まえられなければ、突合は規則の単一化を担保していない。
    """
    period = fb.fixed_period_seconds(_REF, tf)

    def gap_bars(last):
        starts = range(int(last) + period * start_offset, int(bar["time"]), period)
        out = []
        for gs in (starts if cap is None else starts[-cap:]):
            closed = fb.forming_bar_from_ticks(gs, gs + period)
            if closed is not None:
                out.append(closed)
        return out

    return gap_bars


@pytest.mark.parametrize(
    ("mutation", "k"),
    [
        ({"cap": 4}, 8),              # 上限を tails 側で再宣言してずらした
        ({"start_offset": 2}, 3),     # 列挙起点を last + 2*period にした
    ],
)
def test_the_parity_check_catches_a_reimplemented_rule(mutation, k) -> None:
    """P-4 検出力: 供給側が規則を書き直すと P-1（窓）か P-2（呼び出し列）が必ず落ちる。"""
    # Arrange
    frame, bar, _period = _parity_setup("1m", k)

    def run(gap_bars):
        with pytest.MonkeyPatch.context() as mp:
            spy = _SynthesisSpy(mp, result=lambda s, e: _closed_at(s))
            mp.setattr(fb, "forming_bar", lambda *a, **kw: bar)
            good = fb.apply_forming_bar(frame, _REF, "1m", _NOW,
                                        synthesize_closed_gaps=True)
            good_calls = list(spy.windows)
        with pytest.MonkeyPatch.context() as mp:
            spy = _SynthesisSpy(mp, result=lambda s, e: _closed_at(s))
            mutant = ltt.window_with_forming(
                frame, bar, inject=fb.inject_forming_bars, gap_bars=gap_bars,
            )
            return (good, good_calls), (mutant, list(spy.windows))

    # Act
    (good, good_calls), (mutant, mutant_calls) = run(
        _mutant_gap_bars("1m", bar, **mutation)
    )

    # Assert — P-1（窓）と P-2（呼び出し列）が **どちらも** 食い違う＝突合に検出力が在る。
    assert mutant_calls != good_calls
    assert _normalize_frame(mutant) != _normalize_frame(good)
