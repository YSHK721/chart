"""常駐がティック実体と M1 の突合を**周期実行の自己修復として持つ**（ISSUE-534 根治）。

用語（初出定義）:
    突合（ティック実体と M1）
        ＝ 窓の日を日別ティック parquet から作り直し、既存 M1 の同区間と行のバイト列で比べて、
          食い違う分を書き直すこと。実体は :func:`marketdata.tick_m1.heal_m1_days_for_series`
          （唯一の定義）であり、本モジュールが持つのは**周期**だけである。
    窓
        ＝ 突合の対象とする UTC 日。:func:`tools.live_tick_watch.heal_days` が決める（前日と当日）。
    1 段下
        ＝ 既存の自己修復（「`marketdata.rollup.heal_tail_gaps`」）が見るのは M1 とロールアップの
          ずれである。その素材側（ティック実体と M1）が「1 段下」であり、そこを見る経路が無かった。

なぜこの検定が要るか（実測された損害・ISSUE-534）:
    追記の口は ``index > 既存最終 date`` の行しか書かないため、**一度書いた分は二度と直らない**。
    分の末尾ティックが書込時点の手元に無かった 178 分は不完全なまま残り、常駐が止まっていた間の
    129 分は欠測のまま残った（2026-09-26 実測・どちらも出力は連続して見える）。
    **猶予秒は原因ではない**——欠けたティックは分が終わる 11.1 秒前〜ちょうどに発生しており、
    :data:`tools.live_tick_watch._STREAM_M1_GRACE_SECONDS`（12 秒）を 1 件も超えていない。
    本検定は待ち時間には一切触れず、「書いた分を見直す経路が周期で回ること」だけを固定する。

本検定が固定するもの:
  M-1 1 周期で突合が発行され、窓は**前日と当日**である（履歴全体を毎回作り直さない）。
  M-2 突合の発行は**組で 1 回**である（系列ごとに口を呼ばない＝組の全系列へ 1 回の走査で効く）。
  M-3 直した分数をログに残す（無言で直さない）。
  M-4 直った周期では、ロールアップの自己修復を**周期待ちさせない**（土台が直ったのに上位足が
      古いまま最大 30 分残る、を作らない）。
  M-5 発行順は 追記 → 突合 → ロールアップ更新（壊れた土台の上に上位足の差分を積まない）。
  M-6 周期: 起動直後の 1 回目は必ず走り、周期内の 2 回目は走らない。
  M-7 結線の実証: 不完全な足を持つ M1 が、常駐の 1 周期で素材どおりに直る。

本検定が固定しないもの（射程の明示）:
  - 突合そのもの（何を直すか・冪等・有界・費用）。それは
    ``marketdata/tests/test_tick_m1_day_heal.py`` が持つ。
  - ロールアップ末尾の整合の中身。それは ``marketdata/tests/test_rollup_tail_heal.py``。

常駐もサーバも起動しない・ネットワークは叩かない（取得の口を差し替える）。書込はすべて
``tmp_path``（``data/marketdata/**`` は 1 バイトも触らない）。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, List

import pandas as pd
import pytest

from marketdata import dataset_registry, tick_m1
from tools import live_tick_watch as ltw

#: 固定の「現在時刻」と素材の 2 日（実時計を読まない・テスト決定性）。
_DAY1 = dt.date(2026, 9, 1)
_DAY2 = dt.date(2026, 9, 2)
_NOW = dt.datetime(2026, 9, 2, 12, 0, 30)


@pytest.fixture(autouse=True)
def fresh_periods(monkeypatch):
    """周期の状態をテスト間で持ち越さない（F.I.R.S.T の Independent）。

    周期はモジュール変数（単調時計の次回時刻）で持つため、前のテストが進めた値が残ると
    「起動直後の 1 回目は必ず走る」が観測できなくなる。
    """
    monkeypatch.setattr(ltw, "_m1_heal_next_monotonic", 0.0)
    monkeypatch.setattr(ltw, "_heal_next_monotonic", 0.0)


def _ledger_refs() -> "tuple[str, ...]":
    """種が指すティック木を読む系列の組（綴りを書き写さない）。"""
    return dataset_registry.series_refs_of(ltw.REF)


def _ticks(day: dt.date, n_ticks: int) -> pd.DataFrame:
    """``day`` の合成ティック（3 分 × ``n_ticks`` 本・bid も気配幅も分内で動く）。

    ``n_ticks`` を減らすと、その分の close / high / volume が変わる（＝不完全な足の素材）。
    """
    rows = [
        (
            pd.Timestamp(day) + pd.Timedelta(minutes=m, seconds=5 + 25 * k),
            66000.0 + 0.1 * m + 0.05 * k,
            66000.0 + 0.1 * m + 0.05 * k + 0.1 * (60 + m + (3, 0, 6)[k]),
        )
        for m in range(3) for k in range(n_ticks)
    ]
    return pd.DataFrame({
        "timestamp": pd.to_datetime([r[0] for r in rows]).tz_localize("UTC"),
        "bidPrice": [r[1] for r in rows],
        "askPrice": [r[2] for r in rows],
    })


def _put_day(data_dir: Path, day: dt.date, n_ticks: int = 3) -> None:
    """``day`` の合成ティックを木へ置く（1 周期が読む素材）。"""
    path = tick_m1.day_parquet_path(day, data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ticks(day, n_ticks).to_parquet(path, index=False)


def _no_fetch(_day: dt.date, _next: dt.date) -> pd.DataFrame:
    """取得の差し替え（常に空＝既存 parquet を温存する。素材は検定が置く）。"""
    return pd.DataFrame()


def _m1_path(ref: str, data_dir: Path) -> Path:
    return tick_m1.m1_csv_path(ref=ref, data_dir=data_dir)


def _cycle(monkeypatch, data_dir: Path, *, now: dt.datetime = _NOW) -> None:
    """1 周期（取得は差し替え・素材は既に木に在る）。"""
    monkeypatch.setattr(ltw, "_fetch_day", _no_fetch)
    ltw.update_once(now, data_dir, interval=60, full_start=_DAY1)


def _spy(monkeypatch, module, name: str) -> "List[tuple]":
    """``module.name`` を包み、発行ごとに ``(args, kwargs)`` を記録する Test Spy。"""
    real = getattr(module, name)
    calls: "List[tuple]" = []

    def recorded(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, recorded)
    return calls


# =====================================================================
# M-1. 1 周期で突合が発行され、窓は前日と当日である
# =====================================================================
def test_a_cycle_reconciles_the_ticks_against_the_m1_for_today_and_yesterday(
    monkeypatch, tmp_path
):
    """M-1: 突合が 1 周期で発行され、窓は前日と当日の 2 日である（履歴を毎回作り直さない）。"""
    # Arrange
    for day in (_DAY1, _DAY2):
        _put_day(tmp_path, day)
    calls = _spy(monkeypatch, tick_m1, "heal_m1_days_for_series")

    # Act
    _cycle(monkeypatch, tmp_path)

    # Assert
    assert len(calls) == 1, f"突合の発行が {len(calls)} 回（周期は 1 回であること）"
    window = [pd.Timestamp(d).date() for d in calls[0][0][0]]
    assert window == [_NOW.date() - dt.timedelta(days=1), _NOW.date()]
    assert ltw.heal_days(_NOW) == window  # 窓の規則の出所は 1 つ（呼出側が別に組まない）


# =====================================================================
# M-2. 突合の発行は組で 1 回
# =====================================================================
def test_the_reconciliation_is_issued_once_for_the_whole_set(monkeypatch, tmp_path):
    """M-2: 組の全系列へ 1 回の呼出で効く（系列ごとに口を呼ばない）。"""
    # Arrange
    for day in (_DAY1, _DAY2):
        _put_day(tmp_path, day)
    calls = _spy(monkeypatch, tick_m1, "heal_m1_days_for_series")

    # Act
    _cycle(monkeypatch, tmp_path)

    # Assert
    assert len(_ledger_refs()) >= 2, "同じ木を読む系列が 1 件（この検定が空振りしている）"
    assert len(calls) == 1
    assert tuple(calls[0][1]["refs"]) == _ledger_refs()


# =====================================================================
# M-3 / M-7. 直った分数がログに残り、実際に直る
# =====================================================================
def test_an_incomplete_bar_is_repaired_within_one_cycle_and_logged(
    monkeypatch, tmp_path, caplog
):
    """M-3 / M-7: 出揃う前に書かれた足が 1 周期で直り、直した分数がログに残る。

    ISSUE-534 の形をそのまま作る: ティックが 2 本の時点で M1 を書き、その後 3 本目が届く。
    追記の口だけでは ``index > 先端`` の行しか書かないため、この足は永久に直らない。
    """
    # Arrange
    _put_day(tmp_path, _DAY1)
    _put_day(tmp_path, _DAY2, n_ticks=2)          # 出揃う前の素材で…
    _cycle(monkeypatch, tmp_path)                 # …M1 を書いてしまう。
    before = {ref: _m1_path(ref, tmp_path).read_bytes() for ref in _ledger_refs()}
    _put_day(tmp_path, _DAY2, n_ticks=3)          # その後ティックが出揃う。
    monkeypatch.setattr(ltw, "_m1_heal_next_monotonic", 0.0)

    # Act
    with caplog.at_level("WARNING"):
        _cycle(monkeypatch, tmp_path)

    # Assert
    reference = tmp_path / "reference"
    for day in (_DAY1, _DAY2):
        _put_day(reference, day)
    tick_m1.build_m1_from_ticks_for_series(
        _DAY1, _DAY2, refs=_ledger_refs(), data_dir=reference
    )
    assert {ref: _m1_path(ref, tmp_path).read_bytes() for ref in _ledger_refs()} == {
        ref: _m1_path(ref, reference).read_bytes() for ref in _ledger_refs()
    }
    assert any(before[ref] != _m1_path(ref, tmp_path).read_bytes() for ref in _ledger_refs())
    assert any("自己修復" in r.getMessage() for r in caplog.records), (
        "直したのに無言で通した（ログに残っていない）"
    )


# =====================================================================
# M-4. 直った周期はロールアップの自己修復を周期待ちさせない
# =====================================================================
def test_a_repair_makes_the_rollup_heal_run_in_the_same_cycle(monkeypatch, tmp_path):
    """M-4: M1 が直った周期では、ロールアップの自己修復が周期を待たずに走る。

    上位足は M1 の派生物である。土台の分バーが直ったのに上位足の検査が最大 30 分先だと、
    その間だけ「M1 と上位足が食い違う」状態が残る（ISSUE-488 と同型の穴を修復自身が作る）。
    """
    # Arrange
    _put_day(tmp_path, _DAY1)
    _put_day(tmp_path, _DAY2, n_ticks=2)
    _cycle(monkeypatch, tmp_path)
    _put_day(tmp_path, _DAY2, n_ticks=3)
    monkeypatch.setattr(ltw, "_m1_heal_next_monotonic", 0.0)
    monkeypatch.setattr(ltw, "_heal_next_monotonic", float("inf"))  # 周期は「まだ来ていない」。
    heals = _spy(monkeypatch, ltw, "_heal_if_due")

    # Act
    _cycle(monkeypatch, tmp_path)

    # Assert
    assert heals, "ロールアップの自己修復が 1 度も照会されていない"
    assert any(call[1].get("force") for call in heals), (
        "M1 を直したのにロールアップの自己修復は周期待ちのままだった"
    )


# =====================================================================
# M-5. 発行順は 追記 → 突合 → ロールアップ更新
# =====================================================================
def test_the_reconciliation_runs_after_the_append_and_before_the_rollups(
    monkeypatch, tmp_path
):
    """M-5: 壊れた土台の上に上位足の差分を積まない（順序を固定する）。"""
    # Arrange
    for day in (_DAY1, _DAY2):
        _put_day(tmp_path, day)
    order: "List[str]" = []

    def stage(name: str, real):
        def recorded(*args, **kwargs):
            order.append(name)
            return real(*args, **kwargs)
        return recorded

    monkeypatch.setattr(ltw, "_append_m1", stage("append", ltw._append_m1))
    monkeypatch.setattr(
        tick_m1, "heal_m1_days_for_series",
        stage("heal", tick_m1.heal_m1_days_for_series),
    )
    monkeypatch.setattr(ltw, "_rollup_update", stage("rollup", ltw._rollup_update))

    # Act
    _cycle(monkeypatch, tmp_path)

    # Assert
    assert order == ["append", "heal", "rollup"]


# =====================================================================
# M-5b. 分境界の連鎖（ストリーミング）にも効く
# =====================================================================
def test_the_streaming_minute_close_chain_also_reconciles(monkeypatch, tmp_path):
    """M-5b: ``--stream``（実運用で走っている経路）の分境界の連鎖も突合を発行する。

    常駐は 2 つの周期を持つ（1 分ループの :func:`tools.live_tick_watch.update_once` と、
    分境界ごとの :func:`tools.live_tick_watch._chain_m1_rollup`）。片方だけに段を足すと、
    実際に走っている側だけが古い連鎖を回し続ける——それが ISSUE-534 で 178 分を残したのと
    同じ型の取り残しである。形成中の境界は猶予を引いた値がそのまま渡ること（書き手より小さい
    値で突合すると、書き手が書いた分が素材側に現れず見送りになる）。
    """
    # Arrange
    for day in (_DAY1, _DAY2):
        _put_day(tmp_path, day)
    calls = _spy(monkeypatch, tick_m1, "heal_m1_days_for_series")

    # Act
    ltw._chain_m1_rollup(_NOW, tmp_path, _DAY1)

    # Assert
    assert len(calls) == 1, f"分境界の連鎖で突合が {len(calls)} 回発行された"
    assert calls[0][1]["until"] == pd.Timestamp(
        _NOW - dt.timedelta(seconds=ltw._STREAM_M1_GRACE_SECONDS)
    ).floor("min")


# =====================================================================
# M-6. 周期（起動直後は走り、周期内の 2 回目は走らない）
# =====================================================================
def test_the_reconciliation_runs_once_per_period(monkeypatch, tmp_path):
    """M-6: 起動直後の 1 回目は必ず走り、周期内の 2 回目は走らない（毎周期は要らない量）。"""
    # Arrange
    for day in (_DAY1, _DAY2):
        _put_day(tmp_path, day)
    calls = _spy(monkeypatch, tick_m1, "heal_m1_days_for_series")

    # Act
    _cycle(monkeypatch, tmp_path)
    first = len(calls)
    _cycle(monkeypatch, tmp_path)

    # Assert
    assert first == 1, f"起動直後の 1 回目が走らなかった（発行 {first} 回）"
    assert len(calls) == 1, f"周期内の 2 回目でも走った（発行 {len(calls)} 回）"
    assert ltw._M1_HEAL_EVERY_SECONDS > 0


# =====================================================================
# 有界（周期の持ち主が渡す窓は 2 日で固定）
# =====================================================================
@pytest.mark.parametrize(
    "now",
    [dt.datetime(2026, 9, 2, 0, 0, 3), dt.datetime(2026, 9, 2, 12, 0, 30),
     dt.datetime(2026, 9, 2, 23, 59, 59)],
)
def test_the_window_is_always_two_days(now: dt.datetime):
    """窓は時刻に依らず前日と当日の 2 日である（純粋・実時計を読まない）。

    「日跨ぎ直後だけ前日を含める」形にすると、周期が日境界を跨いだ瞬間に居たかどうかで
    前日が突合されるか否かが変わる（居なければ前日の食い違いは永久に残る）。窓の広さは
    2 日で固定し、費用は履歴に依らず一定に保つ。
    """
    # Arrange / Act
    window = ltw.heal_days(now)

    # Assert
    assert window == [now.date() - dt.timedelta(days=1), now.date()]
    assert len(window) == 2
