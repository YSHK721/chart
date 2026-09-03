"""窓供給の失敗は当該計算足グループだけを落とす（ISSUE-479 2 巡目レビュー 🟡-4）。

なぜ必要か（設計上の非対称）:
    ``/live_ticks`` の末尾値は計算足グループごとに独立して組まれる。窓のロード
    （_load_window）・seed の解決（_bar_seed）・末尾値の計算（tails_for_ticks）は
    いずれも「失敗したグループだけ落とし、他のグループの末尾値は出す」縮退契約に
    入っていた。ところが窓供給（window_with_forming）だけがその契約の外
    （group 単位 try の手前）にあり、
    注入が例外を投げると ``/live_ticks`` の応答**全体**が落ちる非対称が残っていた。
    注入は事前条件を緩めない設計（欠けた OHLCV キーで ``KeyError``）なので、
    上流が壊れた瞬間に全指標のティック更新が止まる経路になっていた。

本ファイルが固定する不変条件:
    1. 窓供給が例外を投げても応答は返り、失敗したグループの末尾値だけが欠ける。
    2. 失敗を無言にしない（例外ログをちょうど 1 回残す）。
    3. 計算量: 窓供給の発行 − 使用した窓 = 0（失敗グループの窓を作って捨てない）。
       グループ数 1 / 2 の 2 点で固定する。

data/: 実データを読まない（合成 DataFrame・注入した偽 port のみ）。
構造: Arrange-Act-Assert（AAA）。
"""

from __future__ import annotations

import logging

import pytest

from adapter.compute import live_tick_tails as ltt
from adapter.controller import live_tick_tails_controller as ctl
from marketdata.tf_meta import bar_time_unix
from test_live_tails_window_is_the_forming_bar import (
    _FORMING_MINUTE,
    _SPEC,
    _TICKS,
    _Port,
    _query,
    _unix,
    _windows_for,
)

_CTL_LOGGER = "adapter.controller.live_tick_tails_controller"

#: 1m グループの窓供給だけを失敗させるための識別子（形成中バーの周期で見分ける）。
_FAILING_BAR_TIME = int(bar_time_unix("1m", _unix(_FORMING_MINUTE)))

#: 計算足 1m（チャート足追従）と 5m の 2 グループ。
_TWO_GROUPS = _SPEC + [
    {"instanceId": "i2", "indicatorId": "profit_rsi", "params": {"timeframe": "5m"}},
]


def _install(monkeypatch, port) -> "tuple[list, list]":
    """port・増分宣言・指標計算を注入し、1m グループの窓供給だけを失敗させる。

    Returns:
        ``(供給に成功した窓, 末尾値計算が受け取った窓)`` の 2 つの記録用リスト。
        前者は「発行した窓供給」、後者は「使用した窓」に対応する。
    """
    supplied: list = []
    used: list = []
    real_supply = ctl.window_with_forming
    real_make = ctl.make_tail_at

    def _supply(window, bar, *, inject):
        if int(bar["time"]) == _FAILING_BAR_TIME:
            raise RuntimeError("窓供給の注入が事前条件違反で失敗した（試験用の注入）")
        out = real_supply(window, bar, inject=inject)
        supplied.append(out)
        return out

    def _make(*, df, adapter, latest_compute, set_last_bar):
        used.append(df)
        return real_make(
            df=df, adapter=adapter,
            latest_compute=latest_compute, set_last_bar=set_last_bar,
        )

    monkeypatch.setattr(ctl, "window_with_forming", _supply)
    monkeypatch.setattr(ctl, "make_tail_at", _make)
    monkeypatch.setattr(ctl, "_dataset_port", lambda: port)
    monkeypatch.setattr(ltt, "is_incremental", lambda *a, **k: True)
    monkeypatch.setattr(
        ctl, "latest_compute",
        lambda *a, **k: [{"name": "v", "data": [{"value": 1.0}]}],
    )
    return supplied, used


# --------------------------------------------------------------------------- #
# 1. 縮退契約（応答は生き、失敗したグループだけが欠ける）
# --------------------------------------------------------------------------- #

def test_a_failing_window_supply_drops_only_its_own_group(monkeypatch) -> None:
    """1m の窓供給が例外を投げても、5m グループの末尾値は返る。"""
    # Arrange
    port = _Port(_windows_for(["1m", "5m"]))
    _install(monkeypatch, port)

    # Act
    out = ctl.handle_live_tick_tails(_query(_TWO_GROUPS), _TICKS)

    # Assert — 応答は落ちず、残ったのは失敗しなかったグループの instanceId だけ。
    assert out is not None
    assert [sorted(entry["tails"]) for entry in out] == [["i2"]] * len(_TICKS)


def test_the_window_supply_failure_is_logged_once(monkeypatch, caplog) -> None:
    """窓供給の失敗は無言にせず、例外つきでちょうど 1 回記録する。

    無言で落とすと「その計算足の指標だけが痕跡なくティック更新から消える」状態になり、
    原因を追えない（ISSUE-278 #3 と同じ規律）。
    """
    # Arrange
    port = _Port(_windows_for(["1m", "5m"]))
    _install(monkeypatch, port)

    # Act
    with caplog.at_level(logging.ERROR, logger=_CTL_LOGGER):
        ctl.handle_live_tick_tails(_query(_TWO_GROUPS), _TICKS)

    # Assert
    records = [r for r in caplog.records if r.name == _CTL_LOGGER]
    assert len(records) == 1
    assert records[0].exc_info is not None


def test_every_group_failing_yields_no_tails(monkeypatch) -> None:
    """全グループの窓供給が失敗したら末尾値は組めない（None＝従来応答のまま）。"""
    # Arrange
    port = _Port(_windows_for(["1m"]))
    _install(monkeypatch, port)

    # Act
    out = ctl.handle_live_tick_tails(_query(_SPEC), _TICKS)

    # Assert
    assert out is None


# --------------------------------------------------------------------------- #
# 2. 計算量テスト（絶対命令）— 縮退経路で窓を作って捨てない
# --------------------------------------------------------------------------- #

def _issued_and_used(monkeypatch, tfs, specs) -> "tuple[int, int, int]":
    """``(窓供給の発行数, 使用した窓の数, 窓を得た計算足グループ数)`` を測る。"""
    port = _Port(_windows_for(tfs))
    supplied, used = _install(monkeypatch, port)
    ctl.handle_live_tick_tails(_query(specs), _TICKS)
    return len(supplied), len(used), len(port.loads)


def test_the_degraded_path_builds_no_window_it_does_not_use(monkeypatch) -> None:
    """発行した窓供給 − 使用した窓 = 0 を、グループ数 1 / 2 の 2 点で固定する。

    失敗したグループの窓を作ってから捨てる実装は、応答（出力）が正しいままなので
    状態検証では原理的に落ちない（ISSUE-450 の失敗モード）。無駄の不在そのものを
    表明する。回数は焼き込まず、SUT が数えたグループ数との関係だけを固定する。
    """
    # Arrange / Act — 点 1: 1 グループ（すべて失敗）／点 2: 2 グループ（1 つ失敗）。
    with pytest.MonkeyPatch.context() as mp:
        one_issued, one_used, one_groups = _issued_and_used(mp, ["1m"], _SPEC)
    with pytest.MonkeyPatch.context() as mp:
        two_issued, two_used, two_groups = _issued_and_used(mp, ["1m", "5m"], _TWO_GROUPS)

    # Assert — 両点で無駄ゼロ、かつ発行はグループ数の増分ぶんだけ増える。
    assert (one_issued - one_used, two_issued - two_used) == (0, 0)
    assert two_issued - one_issued == two_groups - one_groups
