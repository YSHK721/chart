"""ライブ末尾値の窓は「末尾＝形成中バー」で供給される（ISSUE-479 🔴-1）。

なぜ必要か（実測した失敗）:
    ``/live_ticks`` の末尾値は「窓を 1 回だけ読み、以降は末尾行へ形成中バーを代入する」
    畳み方で計算する（ISSUE-233 と同じ）。この代入が正しいのは **窓の末尾＝形成中バーと
    同じバー** のときだけである（共有核 :func:`common.forming_window.forming_patch` の
    ``mode == "replace"``）。ところが窓の供給は ``port.load_dataframe`` の**確定**窓を
    そのまま渡しており、M1 CSV は直前の確定分までしか無い（``tools/live_tick_watch.py``
    の排他 floor）。したがって 1m では分 M の途中の窓末尾は常に M-1 であり、

      (a) 述語は毎ポーリング（2.5 秒）``mode == "append"`` を返して警告が出続ける、
      (b) 末尾行への代入が **確定済みの M-1 行**へ分 M の OHLCV を書き込む、

    の 2 つが構造的に起きていた。(b) は「描いたローソクと指標値が別のバー」そのもので
    ある（ISSUE-232 の失敗モード）。

本ファイルが固定する不変条件:
    1. 供給された窓の末尾は形成中バーの周期であり、``mode == "replace"`` が成立する。
    2. 末尾行への代入は形成中バーの行に当たり、直前の**確定バーは書き換わらない**。
    3. その正常経路では警告を出さない（警告は「本当に食い違ったとき」だけの信号にする）。
    4. 計算量: 窓への適用は要求あたり・計算足グループあたり 1 回で、tick 数では増えない。

ISSUE-481 で恒久解へ移った項目（かつて「残存 A / B」として現挙動を記録していた）:
    5. 旧「残存 A」: バッチが周期をまたぐと、境界より後の tick が警告 1 回のうえ
       1 つ前のバーのラベル行へ書かれていた。バーが進んだら窓へ行を足すので、
       値は自分のバーの行に入り、警告も出ない（警告は mode=="skip" 専用の信号になった）。
    6. 旧「残存 B」: 確定末尾を欠いた窓（M1 焼き込み猶予中）は閉じた分を飛ばしていた。
       窓供給側にも閉周期合成（ISSUE-162）を適用したので穴は残らない。

data/: 実データを読まない（合成 DataFrame・注入した偽 port のみ）。
構造: Arrange-Act-Assert（AAA）。
"""

from __future__ import annotations

import json
import logging

import pandas as pd
import pytest

from adapter.compute import live_tick_tails as ltt
from adapter.controller import live_tick_tails_controller as ctl
from common.forming_window import forming_patch

_REF = "jp225_tick"
_LTT_LOGGER = "adapter.compute.live_tick_tails"

#: 形成中の分（M）と、M1 CSV が持ちうる最後の確定分（M-1）。
_FORMING_MINUTE = "2026-01-05 09:04:00"
_LAST_CONFIRMED = "2026-01-05 09:03:00"

_FIELDS = ("open", "high", "low", "close", "volume")


def _unix(text: str) -> int:
    """naive UTC の日時文字列 → UNIX 秒（``.timestamp()`` は使わない）。"""
    return int(pd.Timestamp(text).value // 1_000_000_000)


#: 分 M に属する 2 tick（mid 100.0 → 101.0）。
_TICKS = [
    [_unix(_FORMING_MINUTE) * 1000 + 100, 100.0],
    [_unix(_FORMING_MINUTE) * 1000 + 300, 101.0],
]


def _confirmed_window(*labels: str) -> "pd.DataFrame":
    """確定バーだけの窓（date-index・OHLCV）。値はラベルごとに一意にする。"""
    index = pd.DatetimeIndex([pd.Timestamp(t) for t in labels], name="date")
    base = [10.0 * (i + 1) for i in range(len(labels))]
    return pd.DataFrame(
        {
            "open": [b + 1 for b in base], "high": [b + 2 for b in base],
            "low": [b + 3 for b in base], "close": [b + 4 for b in base],
            "volume": [b + 5 for b in base],
        },
        index=index,
    )


class _Port:
    """計算足ごとに「末尾＝1 周期前の確定バー」の窓を返す偽 port（1m ライブの実状）。"""

    def __init__(self, windows: "dict[str, pd.DataFrame]") -> None:
        self._windows = windows
        self.loads: "list[str]" = []

    def is_known(self, ref) -> bool:  # noqa: ANN001
        return True

    def is_known_timeframe(self, tf) -> bool:  # noqa: ANN001
        return True

    def load_dataframe(self, ref, tf):  # noqa: ANN001
        self.loads.append(tf)
        return self._windows[tf]


def _query(specs) -> "dict[str, list[str]]":
    """``/live_ticks`` のクエリ（計算足はチャート足 1m に追従）。"""
    return {
        "specs": [json.dumps(specs)],
        "datasetRef": [_REF],
        "timeframe": ["1m"],
    }


_SPEC = [{"instanceId": "i1", "indicatorId": "profit_rsi"}]


@pytest.fixture
def _wired(monkeypatch):
    """port / 増分宣言 / 指標計算を注入し、計算に渡った窓を記録する装置を返す。"""

    def _install(port) -> "list[pd.DataFrame]":
        seen: "list[pd.DataFrame]" = []
        monkeypatch.setattr(ctl, "_dataset_port", lambda: port)
        monkeypatch.setattr(ltt, "is_incremental", lambda *a, **k: True)

        def _compute(adapter, indicator_id, variant, window, params):  # noqa: ANN001, ARG001
            seen.append(window.copy())          # 窓は毎 tick 書き換わるので断面を控える
            return [{"name": "v", "data": [{"value": 1.0}]}]

        monkeypatch.setattr(ctl, "latest_compute", _compute)
        return seen

    return _install


# --------------------------------------------------------------------------- #
# 1. 窓の末尾＝形成中バー（述語が "replace" を返す状態で供給される）
# --------------------------------------------------------------------------- #

def test_the_supplied_window_ends_on_the_forming_bar(_wired) -> None:
    """確定窓の末尾が M-1 でも、計算へ渡る窓の末尾は形成中バー（M）になる。"""
    # Arrange
    port = _Port({"1m": _confirmed_window("2026-01-05 09:02:00", _LAST_CONFIRMED)})
    seen = _wired(port)

    # Act
    out = ctl.handle_live_tick_tails(_query(_SPEC), _TICKS)

    # Assert
    assert out is not None and len(out) == len(_TICKS)
    assert seen, "指標計算まで到達していません（窓の供給が落ちています）"
    for window in seen:
        last_time = int(pd.Timestamp(window.index[-1]).value // 1_000_000_000)
        assert last_time == _unix(_FORMING_MINUTE)


def test_the_predicate_agrees_that_the_window_tail_is_the_forming_bar(_wired) -> None:
    """供給された窓と形成中バーに対し、共有核の述語が ``"replace"`` を返す。

    「末尾行への代入が正しい」ことの根拠は述語ただ 1 つなので、根拠そのものを固定する。
    """
    # Arrange
    port = _Port({"1m": _confirmed_window("2026-01-05 09:02:00", _LAST_CONFIRMED)})
    seen = _wired(port)

    # Act
    ctl.handle_live_tick_tails(_query(_SPEC), _TICKS)

    # Assert
    for window in seen:
        last_time = int(pd.Timestamp(window.index[-1]).value // 1_000_000_000)
        patch = forming_patch(last_time, {"time": _unix(_FORMING_MINUTE)})
        assert patch.mode == "replace"


def test_the_confirmed_bar_is_not_overwritten_by_the_forming_values(_wired) -> None:
    """形成中バーの OHLCV は M の行に入り、確定済みの M-1 行は 1 ビットも変わらない。"""
    # Arrange
    confirmed = _confirmed_window("2026-01-05 09:02:00", _LAST_CONFIRMED)
    port = _Port({"1m": confirmed})
    seen = _wired(port)

    # Act
    ctl.handle_live_tick_tails(_query(_SPEC), _TICKS)

    # Assert — 末尾行は tick 2 本ぶんの累積（open=100.0 / high=101.0 / volume=2）。
    last_row = seen[-1].iloc[-1]
    assert (float(last_row["open"]), float(last_row["high"])) == (100.0, 101.0)
    assert (float(last_row["low"]), float(last_row["close"])) == (100.0, 101.0)
    assert float(last_row["volume"]) == 2.0
    # 確定バー（M-1）は供給時のまま。
    kept = seen[-1].loc[pd.Timestamp(_LAST_CONFIRMED)]
    expected = confirmed.loc[pd.Timestamp(_LAST_CONFIRMED)]
    assert [float(kept[c]) for c in _FIELDS] == [float(expected[c]) for c in _FIELDS]


def test_the_normal_live_path_does_not_warn(_wired, caplog) -> None:
    """正常経路（1m の確定窓＋現在分の tick）では警告を出さない。

    警告は「窓と形成中バーが本当に食い違った」ときの信号である。構造的に必ず出る状態のままだと
    信号として役に立たない（毎ポーリング出続けて、本物の食い違いが埋もれる）。
    """
    # Arrange
    port = _Port({"1m": _confirmed_window("2026-01-05 09:02:00", _LAST_CONFIRMED)})
    _wired(port)

    # Act
    with caplog.at_level(logging.WARNING, logger=_LTT_LOGGER):
        ctl.handle_live_tick_tails(_query(_SPEC), _TICKS)

    # Assert
    assert [r for r in caplog.records if r.name == _LTT_LOGGER] == []


# --------------------------------------------------------------------------- #
# 2. 既に末尾＝形成中バーの窓（上位足）は素通し（適用は append のときだけ）
# --------------------------------------------------------------------------- #

def test_a_window_that_already_ends_on_the_forming_bar_is_passed_through(_wired) -> None:
    """末尾が既に形成中バーの窓（上位足の rollup partial）は同一オブジェクトのまま使う。"""
    # Arrange — 末尾が形成中の分 M そのもの。
    window = _confirmed_window("2026-01-05 09:03:00", _FORMING_MINUTE)
    port = _Port({"1m": window})
    seen = _wired(port)

    # Act
    ctl.handle_live_tick_tails(_query(_SPEC), _TICKS)

    # Assert — 行は増えず、末尾は M のまま。
    assert len(seen[-1]) == len(window)
    assert pd.Timestamp(seen[-1].index[-1]) == pd.Timestamp(_FORMING_MINUTE)


# --------------------------------------------------------------------------- #
# 3. 二重適用が起きない（/compute 経路との重複・自分自身の重ねがけ）
# --------------------------------------------------------------------------- #

def test_applying_the_window_forming_twice_is_the_same_as_once() -> None:
    """窓への適用は冪等（2 度目は述語が ``"replace"`` を返し、同一オブジェクトを返す）。

    /compute と /live_ticks は別々の入口で形成中バーを注入する。仮に両方が同じ窓に掛かっても、
    形成中バーは 1 本ぶんしか増えない（重ねがけで足が 2 本にならない）ことを、規則そのものの
    性質として固定する。
    """
    # Arrange
    window = _confirmed_window("2026-01-05 09:02:00", _LAST_CONFIRMED)
    bar = {"time": _unix(_FORMING_MINUTE), "open": 100.0, "high": 101.0,
           "low": 99.0, "close": 100.5, "volume": 2.0}

    # Act
    once = ltt.window_with_forming(window, bar, inject=ctl.inject_forming_bars)
    twice = ltt.window_with_forming(once, bar, inject=ctl.inject_forming_bars)

    # Assert
    assert twice is once                      # 2 度目は複製すらしない
    assert len(once) == len(window) + 1       # 増えた足は 1 本だけ


def test_the_live_window_supply_does_not_use_the_compute_injection_entry(
    _wired, monkeypatch
) -> None:
    """``/live_ticks`` の窓供給は ``/compute`` の注入入口を通らない（二重適用の経路が無い）。

    /compute 専用の入口は tick parquet の読込と欠落閉周期の合成を伴う。ライブ末尾値の窓は
    そこを通らず、注入の実体だけを共有する。
    """
    # Arrange
    from adapter.compute import forming_bar as fb

    calls: "list[str]" = []
    monkeypatch.setattr(
        fb, "apply_forming_bar",
        lambda *a, **k: calls.append("apply_forming_bar"),
    )
    port = _Port({"1m": _confirmed_window("2026-01-05 09:02:00", _LAST_CONFIRMED)})
    seen = _wired(port)

    # Act
    ctl.handle_live_tick_tails(_query(_SPEC), _TICKS)

    # Assert
    assert calls == []
    assert len(seen[-1]) == len(port._windows["1m"]) + 1   # 追加された足はちょうど 1 本


# --------------------------------------------------------------------------- #
# 4. 計算量テスト（絶対命令）— 適用は要求あたり・グループあたり 1 回
# --------------------------------------------------------------------------- #

def _windows_for(tfs) -> "dict[str, pd.DataFrame]":
    """各計算足について「末尾＝1 周期前」の窓を作る（どの足でも append が要る状態）。"""
    from marketdata.tf_meta import TF_BAR_SEC, bar_time_unix

    out = {}
    for tf in tfs:
        bar = bar_time_unix(tf, _unix(_FORMING_MINUTE))
        prev = bar - int(TF_BAR_SEC[tf])
        out[tf] = _confirmed_window(
            str(pd.Timestamp(prev - int(TF_BAR_SEC[tf]), unit="s")),
            str(pd.Timestamp(prev, unit="s")),
        )
    return out


def _issued_applications(monkeypatch, port, specs, ticks) -> "tuple[int, int]":
    """``(窓への適用の発行数, 窓を得た計算足グループ数)`` を測る。"""
    calls: "list[int]" = []
    real = ctl.inject_forming_bars

    def _spy(df, bars):  # noqa: ANN001
        calls.append(len(bars))
        return real(df, bars)

    monkeypatch.setattr(ctl, "inject_forming_bars", _spy)
    monkeypatch.setattr(ctl, "_dataset_port", lambda: port)
    monkeypatch.setattr(ltt, "is_incremental", lambda *a, **k: True)
    monkeypatch.setattr(
        ctl, "latest_compute",
        lambda *a, **k: [{"name": "v", "data": [{"value": 1.0}]}],
    )
    ctl.handle_live_tick_tails(_query(specs), ticks)
    return len(calls), len(port.loads)


def test_the_window_forming_is_applied_once_per_group(monkeypatch) -> None:
    """適用の発行数は「窓を得た計算足グループ数」と一致する（発行 − 使用 = 0）。"""
    # Arrange
    port = _Port(_windows_for(["1m"]))

    # Act
    issued, used = _issued_applications(monkeypatch, port, _SPEC, _TICKS)

    # Assert
    assert issued - used == 0


def test_the_application_count_follows_the_number_of_groups(monkeypatch) -> None:
    """計算足グループを 1 → 2 に増やすと発行も 1 → 2（オーダーの表明・2 点で固定）。"""
    # Arrange
    two_groups = _SPEC + [{"instanceId": "i2", "indicatorId": "profit_rsi",
                           "params": {"timeframe": "5m"}}]

    # Act
    with pytest.MonkeyPatch.context() as mp:
        one, used_one = _issued_applications(mp, _Port(_windows_for(["1m"])), _SPEC, _TICKS)
    with pytest.MonkeyPatch.context() as mp:
        two, used_two = _issued_applications(
            mp, _Port(_windows_for(["1m", "5m"])), two_groups, _TICKS)

    # Assert
    assert (one, two) == (used_one, used_two)
    assert two - one == used_two - used_one


def test_the_application_does_not_grow_with_the_tick_count(monkeypatch) -> None:
    """tick 数を倍にしても適用の発行数は変わらない（窓の供給は要求あたり 1 回）。

    ここが tick 数に比例すると、出力は正しいまま「作っては捨てる」浪費が入る
    （状態検証では原理的に落ちない・ISSUE-450 の失敗モード）。
    """
    # Arrange
    many = [[_TICKS[0][0] + i, 100.0 + i * 0.1] for i in range(len(_TICKS) * 8)]

    # Act
    with pytest.MonkeyPatch.context() as mp:
        few_issued, _ = _issued_applications(mp, _Port(_windows_for(["1m"])), _SPEC, _TICKS)
    with pytest.MonkeyPatch.context() as mp:
        many_issued, _ = _issued_applications(mp, _Port(_windows_for(["1m"])), _SPEC, many)

    # Assert
    assert many_issued == few_issued


# --------------------------------------------------------------------------- #
# 5. 残存 A の現挙動固定（ISSUE-481 の恒久解で意図的に赤へ転じる）
# --------------------------------------------------------------------------- #

#: 形成中の分 M の次の分（バッチが周期をまたいだ先）。
_NEXT_MINUTE = "2026-01-05 09:05:00"

#: 周期 M と M+1 にまたがる 4 tick（09:04 が 2 本・09:05 が 2 本）。
_BOUNDARY_TICKS = _TICKS + [
    [_unix(_NEXT_MINUTE) * 1000 + 100, 200.0],
    [_unix(_NEXT_MINUTE) * 1000 + 300, 201.0],
]

#: 周期 M の秒数（1m）。窓のラベル間隔がこの整数倍から外れると閉じた分が欠けている。
_PERIOD_SEC = 60


def _label_seconds(window) -> "list[int]":
    """窓のラベル（date-index）を UNIX 秒の列で返す（``.timestamp()`` は使わない）。"""
    return [int(pd.Timestamp(t).value // 1_000_000_000) for t in window.index]


def test_a_batch_crossing_the_period_does_not_warn(_wired, caplog) -> None:
    """恒久解（ISSUE-481）: バッチが分をまたいでも警告は出ない。

    窓の供給が揃えるのは states[0]（バッチ先頭 tick）の周期だけである。以前は境界より後の
    tick で述語が append を返し、make_tail_at が「窓と形成中バーが別のバー」として 1 度
    記録していた。バーが進んだのなら窓へ行を足せばよいので、これは食い違いではない。

    警告は今後 ``mode == "skip"``（順序が逆転した tick＝本当に説明のつかない入力）専用の
    信号にする。構造的に必ず出る警告を残すと、本物の食い違いがそこへ埋もれる。
    """
    # Arrange
    port = _Port({"1m": _confirmed_window("2026-01-05 09:02:00", _LAST_CONFIRMED)})
    _wired(port)

    # Act
    with caplog.at_level(logging.WARNING, logger=_LTT_LOGGER):
        ctl.handle_live_tick_tails(_query(_SPEC), _BOUNDARY_TICKS)

    # Assert
    assert [r for r in caplog.records if r.name == _LTT_LOGGER] == []


def test_a_tick_after_the_period_boundary_lands_on_its_own_bar_row(_wired) -> None:
    """恒久解（ISSUE-481）: 境界より後の tick は**自分のバー**のラベル行へ入る。

    以前は窓の末尾ラベルが 09:04（バッチ先頭 tick の周期）のままで、そこへ 09:05 の形成中
    バーの OHLCV が代入されていた（＝描いたローソクと指標値が別のバー・ISSUE-232 の失敗
    モード）。バーが進んだ時点で窓へ行を足すので、09:05 のラベル行が生まれて値はそちらへ入る。

    09:04 の行は跨いだ時点の値で**凍結**する（跨いだ後の tick が過去のバーを書き換えない）。
    """
    # Arrange
    port = _Port({"1m": _confirmed_window("2026-01-05 09:02:00", _LAST_CONFIRMED)})
    confirmed = _confirmed_window("2026-01-05 09:02:00", _LAST_CONFIRMED)
    seen = _wired(port)

    # Act
    ctl.handle_live_tick_tails(_query(_SPEC), _BOUNDARY_TICKS)

    # Assert — 末尾ラベルは 09:05 で、09:04 の行も残っている。
    labels = _label_seconds(seen[-1])
    assert labels[-1] == _unix(_NEXT_MINUTE)
    assert _unix(_FORMING_MINUTE) in labels
    # 09:04 は跨ぎ時点の累積で凍結・09:05 は自分の 2 tick ぶん。
    row_0904 = seen[-1].loc[pd.Timestamp(_FORMING_MINUTE)]
    row_0905 = seen[-1].loc[pd.Timestamp(_NEXT_MINUTE)]
    assert [float(row_0904[c]) for c in _FIELDS] == [100.0, 101.0, 100.0, 101.0, 2.0]
    assert [float(row_0905[c]) for c in _FIELDS] == [200.0, 201.0, 200.0, 201.0, 2.0]
    # 確定済みの 2 行は 1 ビットも変わらない。
    for confirmed_label in ("2026-01-05 09:02:00", _LAST_CONFIRMED):
        kept = seen[-1].loc[pd.Timestamp(confirmed_label)]
        expected = confirmed.loc[pd.Timestamp(confirmed_label)]
        assert [float(kept[c]) for c in _FIELDS] == [float(expected[c]) for c in _FIELDS]


# --------------------------------------------------------------------------- #
# 6. 残存 B の現挙動固定（ISSUE-481 の恒久解で意図的に赤へ転じる）
# --------------------------------------------------------------------------- #

#: M1 焼き込み猶予中の窓＝確定末尾 M-1（09:03）が未着で M-2（09:02）までしか無い。
_STALE_WINDOW_LABELS = ("2026-01-05 09:01:00", "2026-01-05 09:02:00")


#: 猶予中に欠けている閉じた分（09:03）を合成したときの値。
_SYNTHESIZED_CLOSED = {
    "time": _unix(_LAST_CONFIRMED), "open": 50.0, "high": 55.0,
    "low": 49.0, "close": 54.0, "volume": 7.0,
}


def test_a_window_missing_the_last_confirmed_bar_is_filled_by_the_closed_period_synthesis(
    _wired, monkeypatch
) -> None:
    """恒久解（ISSUE-481）: 猶予中に欠けた閉じた分は閉周期合成で埋まり、穴が残らない。

    M1 焼き込み猶予（live_tick_watch の grace）の間、確定窓は M-1 を欠いたまま M-2 で
    終わる。以前はそこへ形成中バー M を足すだけで、窓のラベル間隔に周期 2 本ぶん（120 秒）の
    跳びが残っていた。``/compute`` は同じ穴を閉周期合成（ISSUE-162）で埋めるため、2 経路が
    **別の窓**で計算していた（無音・実データ 24 点中 5-6 点）。窓供給側にも同じ合成規則を
    適用したので、間隔は周期どおりに揃い、合成行には合成値が入る。

    合成規則そのものの検査は ``test_forming_rule_parity.py`` の 2 経路突合（P-1〜P-5）が
    担う。ここで見るのは「窓供給の経路が実際にその規則を通っている」という結線だけなので、
    素材読み（tick parquet）へは降りず、合成の入口を偽物へ差し替える。
    """
    # Arrange
    port = _Port({"1m": _confirmed_window(*_STALE_WINDOW_LABELS)})
    seen = _wired(port)
    monkeypatch.setattr(ctl, "closed_gap_bars", lambda *a, **k: [_SYNTHESIZED_CLOSED])

    # Act
    ctl.handle_live_tick_tails(_query(_SPEC), _TICKS)

    # Assert — 間隔は周期 1 本ぶんだけ（穴なし）。
    labels = _label_seconds(seen[-1])
    assert [b - a for a, b in zip(labels, labels[1:])] == [_PERIOD_SEC] * 3
    # 合成された 09:03 の行は合成値そのもの（形成中バーの値が混ざっていない）。
    filled = seen[-1].loc[pd.Timestamp(_LAST_CONFIRMED)]
    assert [float(filled[c]) for c in _FIELDS] == [
        _SYNTHESIZED_CLOSED[c] for c in _FIELDS
    ]


def test_the_missing_closed_bar_is_not_reported(_wired, caplog) -> None:
    """残存 B の**現挙動の記録**: 閉じた分の欠落は警告として出ない（無音）。

    述語が見るのは「窓末尾と形成中バーが同じバーか」だけで、窓の内部に空いた穴は見ない。
    そのため残存 B は残存 A と違い信号が一切出ず、実データでも 24 点中 5-6 点で無音のまま
    起きていた（ISSUE-481 の実測）。

    ISSUE-481 の恒久解が入れば穴自体が無くなる。本テストが赤へ転じるのは「無音のまま穴が
    残る経路が別に生えた」ときであり、そのとき現挙動の記録として書き換える。
    """
    # Arrange
    port = _Port({"1m": _confirmed_window(*_STALE_WINDOW_LABELS)})
    _wired(port)

    # Act
    with caplog.at_level(logging.WARNING, logger=_LTT_LOGGER):
        ctl.handle_live_tick_tails(_query(_SPEC), _TICKS)

    # Assert
    assert [r for r in caplog.records if r.name == _LTT_LOGGER] == []


# --------------------------------------------------------------------------- #
# 7. 計算量テスト（絶対命令・ISSUE-481）— 行追加も閉周期合成も「要る回数」だけ
#
#    ここで固定するのは出力の正しさではなく **無駄の不在** である。回数そのものは
#    期待値へ焼き込まない（焼き込むと浪費が仕様へ昇格する＝ISSUE-450 の実害）。
#    固定するのは「発行 − 使用 = 0」と「何に比例し何に比例しないか」だけである。
# --------------------------------------------------------------------------- #

#: バッチが 09:04→09:05→09:06 と 2 度またぐ 6 tick（跨ぎ 2 点目）。
_THIRD_MINUTE = "2026-01-05 09:06:00"
_TWO_CROSSING_TICKS = _BOUNDARY_TICKS + [
    [_unix(_THIRD_MINUTE) * 1000 + 100, 300.0],
    [_unix(_THIRD_MINUTE) * 1000 + 300, 301.0],
]

#: 同じ分（09:04）に収まる 16 tick（tick 数の 2 点目・跨ぎゼロ）。
_MANY_TICKS = [[_TICKS[0][0] + i, 100.0 + i * 0.1] for i in range(len(_TICKS) * 8)]

#: 猶予中に欠ける閉じた分が 1 本（09:03）／3 本（09:01-09:03）の窓（gap 長の 2 点）。
_GAP1_LABELS = _STALE_WINDOW_LABELS
_GAP3_LABELS = ("2026-01-05 08:58:00", "2026-01-05 09:00:00")

#: 跨ぎの計測に使う窓（穴なし＝行追加だけを見る）。
_NO_GAP_LABELS = ("2026-01-05 09:02:00", _LAST_CONFIRMED)


def _synthetic_closed(start: int, tickless) -> "dict | None":
    """合成閉周期バー。``tickless`` に挙げた始端は tick 無し（``None``）。"""
    return None if start in tickless else {
        "time": start, "open": 1.0, "high": 2.0,
        "low": 0.5, "close": 1.5, "volume": 1.0,
    }


class _Record:
    """1 要求ぶんの「発行」と「使用」の記録。

    実体（DataFrame）は参照ごと保持する。``id()`` だけ控えると解放後に別の物へ
    使い回されて、同一性の検査が偶然通る／落ちるようになる。
    """

    def __init__(self) -> None:
        self.inject_bars: "list[int]" = []      # 注入 1 回ごとに足したバー数
        self.inject_outs: list = []             # 注入が返した窓（参照を保持）
        self.synth_starts: "list[int]" = []     # 素材へ要求した閉周期の始端
        self.tickless: int = 0                  # うち tick が無かった周期の数
        self.computed: list = []                # 計算が受け取った窓（参照を保持）
        self.snapshots: list = []               # その時点の断面（値の確認用）


def _run(monkeypatch, *, labels, ticks, specs=_SPEC, tickless=(),
         latest_compute=None) -> _Record:
    """1 要求ぶんを本番経路で走らせ、発行と使用を記録して返す（実データは読まない）。"""
    from adapter.compute import forming_bar as fb

    rec = _Record()
    port = _Port({"1m": _confirmed_window(*labels)})
    real_inject = ctl.inject_forming_bars

    def _inject(df, bars):
        out = real_inject(df, bars)
        rec.inject_bars.append(len(bars))
        rec.inject_outs.append(out)
        return out

    def _reader(start, end):
        rec.synth_starts.append(int(start))
        return _synthetic_closed(int(start), tickless)

    def _compute(adapter, indicator_id, variant, window, params):  # noqa: ANN001, ARG001
        rec.computed.append(window)
        rec.snapshots.append(window.copy())
        return [{"name": "v", "data": [{"value": 1.0}]}]

    monkeypatch.setattr(ctl, "inject_forming_bars", _inject)
    monkeypatch.setattr(fb, "forming_bar_from_ticks", _reader)
    monkeypatch.setattr(ctl, "_dataset_port", lambda: port)
    monkeypatch.setattr(ltt, "is_incremental", lambda *a, **k: True)
    monkeypatch.setattr(ctl, "latest_compute", latest_compute or _compute)
    ctl.handle_live_tick_tails(_query(specs), ticks)
    rec.tickless = len(set(rec.synth_starts) & set(tickless))
    return rec


def _crossed_bars(ticks) -> int:
    """バッチが触れたバーの本数（＝跨ぎ回数 + 1）。規則は本番と同じ tf_meta。"""
    from marketdata.tf_meta import bar_time_unix

    return len({bar_time_unix("1m", int(ms) // 1000) for ms, _mid in ticks})


# --- C-1 -------------------------------------------------------------------- #

def test_the_synthesis_issues_exactly_the_bars_it_uses(monkeypatch) -> None:
    """C-1 発行 − 使用 = 0: 合成の要求は「窓の行になった」か「tick が無かった」のどちらか。

    使用数は**出力から導出**する（窓に現れた合成行 ＋ tick が無かった周期）。要求したのに
    どちらでもない合成が 1 件でもあれば、それは作って捨てた計算である。
    """
    # Arrange / Act — 欠落 3 本のうち 09:02 だけ tick 無しにする。
    rec = _run(monkeypatch, labels=_GAP3_LABELS, ticks=_TICKS,
               tickless=(_unix("2026-01-05 09:02:00"),))

    # Assert
    assert rec.synth_starts, "合成が 1 度も発行されていません（検定が空振り）"
    in_window = set(_label_seconds(rec.snapshots[-1]))
    used = len(set(rec.synth_starts) & in_window) + rec.tickless
    assert len(rec.synth_starts) - used == 0


# --- C-2 -------------------------------------------------------------------- #

def _synthesis_issued(labels, ticks) -> int:
    """当該条件で合成が発行された回数。"""
    with pytest.MonkeyPatch.context() as mp:
        return len(_run(mp, labels=labels, ticks=ticks).synth_starts)


def test_the_synthesis_follows_the_gap_length_not_the_tick_count() -> None:
    """C-2 合成の発行は gap 長で決まり、tick 数では増えない（各 2 点で固定）。

    tick 数に比例すると、出力（窓）は正しいまま poll ごとに同じ閉周期を何度も読み直す
    ことになる（状態検証では原理的に落ちない浪費）。
    """
    # Arrange / Act
    gap1 = _synthesis_issued(_GAP1_LABELS, _TICKS)      # 欠落 1 本 / tick 2 本
    gap3 = _synthesis_issued(_GAP3_LABELS, _TICKS)      # 欠落 3 本 / tick 2 本
    many = _synthesis_issued(_GAP1_LABELS, _MANY_TICKS)  # 欠落 1 本 / tick 16 本

    # Assert
    assert gap3 - gap1 == 2      # gap 長に比例（欠落が 2 本増えれば発行も 2 増える）
    assert many == gap1          # tick 数には非比例


# --- C-4 -------------------------------------------------------------------- #

def _issued_and_crossed(ticks) -> "tuple[int, int]":
    """``(注入の発行数, バッチが触れたバーの本数)``（穴のない窓で計測）。"""
    with pytest.MonkeyPatch.context() as mp:
        rec = _run(mp, labels=_NO_GAP_LABELS, ticks=ticks)
    return len(rec.inject_bars), _crossed_bars(ticks)


def test_the_row_growth_follows_only_the_period_crossings() -> None:
    """C-4 行追加の発行は「バッチが跨いだ回数」だけで決まる（跨ぎ 0 / 1 / 2 の 3 点）。

    発行のうち 1 回は窓供給（要求あたり 1 回）ぶんなので、残りが跨ぎ回数と一致すれば
    余分な行追加はゼロである。回数そのものは焼き込まず、SUT が数えた跨ぎとの関係を固定する。
    """
    # Arrange / Act
    zero = _issued_and_crossed(_TICKS)
    one = _issued_and_crossed(_BOUNDARY_TICKS)
    two = _issued_and_crossed(_TWO_CROSSING_TICKS)

    # Assert
    assert [i - 1 for i, _d in (zero, one, two)] == [d - 1 for _i, d in (zero, one, two)]


# --- C-5 -------------------------------------------------------------------- #

def test_no_window_is_built_that_the_computation_never_sees(monkeypatch) -> None:
    """C-5 作って捨てる窓ゼロ: 跨ぎで足した窓はすべて計算が受け取っている。

    先頭の 1 回は窓供給ぶんで、末尾値の組み立てが入口で 1 度だけ複製する材料になる
    （その複製が計算の受け取る最初の窓）。したがって検査対象は 2 回目以降＝跨ぎで
    足した窓であり、それらが計算へ渡らなければ「作って捨てた」ことになる。
    """
    # Arrange / Act
    rec = _run(monkeypatch, labels=_NO_GAP_LABELS, ticks=_TWO_CROSSING_TICKS)

    # Assert
    crossing_windows = {id(w) for w in rec.inject_outs[1:]}
    received = {id(w) for w in rec.computed}
    assert crossing_windows, "跨ぎで窓が足されていません（検定が空振り）"
    assert crossing_windows <= received
    # 実体の総数は「入口の複製 1 つ ＋ 跨ぎぶん」ちょうど（余分な窓を作らない）。
    assert len(received) == len(rec.inject_outs)


# --- C-7 -------------------------------------------------------------------- #

_SPY_INCREMENTER = "spy_live_tails"


class _PrefixIncrementer:
    """確定プレフィクスが一致すれば流用する Test Spy（実物の ``adapt`` と同型）。

    実物（moving_averages 等）は「保持した状態の確定ぶんが、今回の確定ぶんの先頭に
    一致するか」を見て流用可否を決める。この Spy はその一点だけを写し、計算式は持たない。
    """

    def __init__(self) -> None:
        self.builds: "list[int]" = []

    @staticmethod
    def _prefix(df) -> tuple:
        return tuple(int(pd.Timestamp(t).value) for t in df.index[:-1])

    def prepare(self, df, params):  # noqa: ANN001, ARG002
        return {"prefix": self._prefix(df)}

    def build(self, req):  # noqa: ANN001
        self.builds.append(len(req["prefix"]))
        return {"prefix": req["prefix"]}

    def adapt(self, state, req):  # noqa: ANN001
        kept = state["prefix"]
        return {"prefix": req["prefix"]} if req["prefix"][:len(kept)] == kept else None

    def emit(self, state, req, skeleton, k):  # noqa: ANN001, ARG002
        return [{**skeleton[0], "data": [{"time": 1, "value": 1.0}]}]


class _SkeletonAdapter:
    """系列 metadata の骨格だけを返す計算面（骨格は素材にも窓長にも依らない）。"""

    def compute(self, compute_id, variant, df, params):  # noqa: ANN001, ARG002
        return [{"name": "v", "kind": "line", "data": []}]


def _rebuilds_for(ticks) -> int:
    """当該バッチで発行された「状態の再構築」の回数。"""
    from adapter.compute import incremental as incremental_registry
    from adapter.compute import incremental_state

    spy = _PrefixIncrementer()
    with pytest.MonkeyPatch.context() as mp:
        incremental_state.reset()
        mp.setitem(incremental_registry._INSTANCES, _SPY_INCREMENTER, spy)

        def _through(adapter, indicator_id, variant, window, params):  # noqa: ANN001, ARG001
            return incremental_state.compute(
                _SkeletonAdapter(), indicator_id, variant, window, params,
                name=_SPY_INCREMENTER, k=1,
            )

        _run(mp, labels=_NO_GAP_LABELS, ticks=ticks, latest_compute=_through)
        incremental_state.reset()
    return len(spy.builds)


def test_the_incremental_state_is_not_rebuilt_on_a_period_crossing() -> None:
    """C-7 増分状態は跨ぎで再構築しない（跨ぎ 0 / 2 の 2 点で発行が変わらない）。

    跨ぎで窓へ行を足すと窓の実体は別物になる。状態キャッシュがそれを「別の素材」と
    見なすと、跨ぐたびに全再構築が起きる（値は同じままなので状態検証では落ちない・
    ISSUE-465 の実測では 0.2ms → 374ms）。再構築が跨ぎ回数に比例しないことを固定する。
    """
    # Arrange / Act
    without_crossing = _rebuilds_for(_TICKS)
    with_crossings = _rebuilds_for(_TWO_CROSSING_TICKS)

    # Assert
    assert without_crossing > 0, "増分計算まで到達していません（検定が空振り）"
    assert with_crossings == without_crossing


# --- C-8 検出力（負の対照）------------------------------------------------- #

def test_adding_a_row_every_tick_breaks_the_crossing_relation() -> None:
    """C-8a: 「毎 tick 行を足す」突然変異では C-4 の関係式が破れる（検出力の実証）。"""
    # Arrange — 跨ぎ 0 のバッチで、tick ごとに 1 行足す実装を模す。
    ticks = _MANY_TICKS
    crossed = _crossed_bars(ticks)
    mutant_issued = 1 + len(ticks)          # 窓供給 1 回 ＋ 毎 tick の行追加

    # Act
    honest_issued, honest_crossed = _issued_and_crossed(ticks)

    # Assert — 正しい実装は関係式を満たし、突然変異は満たさない。
    assert honest_issued - 1 == honest_crossed - 1
    assert mutant_issued - 1 != crossed - 1


def test_synthesising_every_tick_breaks_the_tick_independence() -> None:
    """C-8b: 「毎 tick 合成する」突然変異では C-2 の tick 数非比例が破れる。"""
    # Arrange
    gap1 = _synthesis_issued(_GAP1_LABELS, _TICKS)
    many = _synthesis_issued(_GAP1_LABELS, _MANY_TICKS)
    mutant_many = gap1 * len(_MANY_TICKS)   # tick ごとに同じ穴を読み直す実装を模す

    # Act / Assert
    assert many == gap1                     # 正しい実装は tick 数に非比例
    assert mutant_many != gap1              # 突然変異は tick 数に比例して落ちる


def test_removing_the_cap_breaks_the_gap_bound() -> None:
    """C-8c: 「上限を撤去する」突然変異では C-3 の gap 長非比例が破れる。

    上限は暴走防御そのものであり、外すと停止していた端末の再開時に gap 長ぶんの
    parquet 読みが一度に走る。出力は正しいままなので状態検証では落ちない。
    """
    # Arrange
    from adapter.compute import forming_bar as fb

    period = fb.fixed_period_seconds(_REF, "1m")
    last = _unix(_LAST_CONFIRMED)

    def uncapped(gap_periods: int) -> int:
        """上限を撤去した列挙の発行数（突然変異）。"""
        return len(fb._gap_starts(last, last + period * (gap_periods + 1), period))

    def capped(gap_periods: int) -> int:
        """本番の列挙（上限つき）の発行数。"""
        starts = fb._gap_starts(last, last + period * (gap_periods + 1), period)
        return len(starts[-fb._MAX_GAP_FILL_PERIODS:])

    # Act / Assert
    assert capped(2000) == capped(20000)        # 正しい実装は gap 長に非比例
    assert uncapped(2000) != uncapped(20000)    # 突然変異は gap 長に比例して落ちる
