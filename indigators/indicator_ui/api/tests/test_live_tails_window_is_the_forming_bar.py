"""ライブ末尾値の窓は「末尾＝形成中バー」で供給される（ISSUE-479 🔴-1）。

なぜ必要か（実測した失敗）:
    ``/live_ticks`` の末尾値は「窓を 1 回だけ読み、以降は末尾行へ形成中バーを代入する」
    畳み方で計算する（ISSUE-233 と同じ）。この代入が正しいのは **窓の末尾＝形成中バーと
    同じバー** のときだけである（共有核 :func:`common.forming_window.forming_patch` の
    ``mode == "replace"``）。ところが窓の供給は ``port.load_dataframe`` の**確定**窓を
    そのまま渡しており、M1 CSV は直前の確定分までしか無い（``tools/live_tick_watch.py``
    の排他 floor）。したがって 1m では分 M の途中の窓末尾は常に M-1 であり、

      (a) 述語は毎ポーリング（2.5 秒）``mode == "append"`` を返して警告が出続ける、
      (b) ``_set_last_bar`` が **確定済みの M-1 行**へ分 M の OHLCV を書き込む、

    の 2 つが構造的に起きていた。(b) は「描いたローソクと指標値が別のバー」そのもので
    ある（ISSUE-232 の失敗モード）。

本ファイルが固定する不変条件:
    1. 供給された窓の末尾は形成中バーの周期であり、``mode == "replace"`` が成立する。
    2. 末尾行への代入は形成中バーの行に当たり、直前の**確定バーは書き換わらない**。
    3. その正常経路では警告を出さない（警告は「本当に食い違ったとき」だけの信号にする）。
    4. 計算量: 窓への適用は要求あたり・計算足グループあたり 1 回で、tick 数では増えない。

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

    ``/compute`` は ``apply_forming_bar`` で、``/live_ticks`` は本経路で形成中バーを注入する。
    仮に両方が同じ窓に掛かっても、形成中バーは 1 本ぶんしか増えない（重ねがけで足が 2 本に
    ならない）ことを、規則そのものの性質として固定する。
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

    ``apply_forming_bar`` は ``forming_bar()``（tick parquet 読込）と欠落閉周期の合成を伴う
    ``/compute`` 専用の入口である。ライブ末尾値の窓はそこを通らず、注入の実体
    （``inject_forming_bars``）だけを共有する。
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
