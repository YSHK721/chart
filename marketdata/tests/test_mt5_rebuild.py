"""UTC 日が閉じた後の M1 再構築の検定（ISSUE-447 段階 1 / 設計 §10 の裁定＝案 b）。

裁定の内容:
    日内増分の M1 には日次統計を要する外れ値除去（日内 close 中央値から ±30% 乖離する分バーの
    除去・ISSUE-107）が**原理的に適用できない**。数本のバーの中央値は日の中央値ではないからで
    ある。よって日中の M1 は暫定値として表示し、UTC 日が閉じた時点（確定 parquet が出来た後）で
    権威経路により当日を再計算し、**差分がある日だけ**該当日区間を原子置換する。

本検定が固定するのは 4 点である:
    1. 再構築後の当日区間が全量経路（``tick_m1.build_m1_from_ticks``）と一致すること
       ＝「確定記録は既存権威と完全一致する」（設計 §10 の要求そのもの）
    2. 当日**以外**の区間を 1 バイトも動かさないこと
    3. 清浄日は**書込 0**（計算量検定 CX-b と整合。差が無いのに書けば、毎日全ファイルを
       書き直す常駐になる）
    4. 読む確定 parquet が当日 1 個だけであること（保存済み日数に比例して増えない）
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from marketdata import rollup, tick_m1
from marketdata.mt5_ticks import fakes, journal, m1_chain, rebuild
from marketdata.mt5_ticks.port import Mt5SupplyError

_TOKEN = "JP225@OANDA-Japan-MT5-Live"
_REF = "jp225_mt5"
_DAY = dt.date(2026, 8, 25)
_PREV = dt.date(2026, 8, 24)

#: 2026-08 は夏（UTC+3）。ラベル ms ＝ UTC ms + 3h。
_SUMMER_OFFSET_MS = 3 * 3600 * 1000


def _label_ms(utc: dt.datetime) -> int:
    return int(utc.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + _SUMMER_OFFSET_MS


def _rows_for(day: dt.date, *, minutes: int, phantom_minutes=()):
    """``day`` の 09:00 から ``minutes`` 分ぶんのティック。

    ``phantom_minutes`` に挙げた分だけ ~15,100 帯（ISSUE-107 と同型の配信欠損ファントム）に
    する。外れ値は**入力の性質**であって検定の都合ではないので、生成規則をここに閉じる。
    """
    start = dt.datetime(day.year, day.month, day.day, 9, 0)
    rows = []
    for m in range(minutes):
        for i in range(20):
            when = start + dt.timedelta(minutes=m, seconds=i * 3)
            price = 15100.0 if m in phantom_minutes else 66000.0 + m * 2.0 + i * 0.1
            rows.append((_label_ms(when), price, price + 10.0))
    return rows, start + dt.timedelta(minutes=minutes)


def _publish(day, rows, until, tmp_path, *, ref=_REF, update_rollups=False):
    """1 日ぶんを「増分経路で受け取った」状態にする（ジャーナル→確定→増分 M1 追記）。"""
    journal.append(day, rows, symbol=_TOKEN, data_dir=tmp_path)
    journal.finalize(day, symbol=_TOKEN, data_dir=tmp_path)
    m1_chain.append_m1_for_closed_minutes(
        rows, ref=ref, data_dir=tmp_path, until=until.replace(tzinfo=dt.timezone.utc)
    )
    if update_rollups:
        m1_chain.update_rollups(ref=ref, data_dir=tmp_path)


def _m1_dates(tmp_path, ref=_REF) -> "list[str]":
    return list(pd.read_csv(tick_m1.m1_csv_path(ref=ref, data_dir=tmp_path))["date"])


@pytest.fixture()
def store(tmp_path):
    return dict(symbol=_TOKEN, ref=_REF, data_dir=tmp_path)


# =====================================================================
# 権威一致: 再構築の入力は「全量経路が作る当日 M1」そのもの
# =====================================================================

def test_the_cleaning_rule_is_the_very_function_the_authority_calls():
    """M-1 と同型: 日次クリーニングの第 2 実装を持たない（同じ関数を指している）。

    データが偶々一致することに頼らず、**参照の同一性**で固定する。権威側がクリーニング規則を
    差し替えたら、その瞬間に本モジュールも一緒に動く（片方だけ直る事故を構造で消す）。
    """
    assert rebuild.clean_day_m1 is tick_m1.outlier_policy.repair_day_outliers


def test_the_authoritative_day_equals_what_the_whole_build_produces(tmp_path, store):
    """再計算の中身が権威（``build_m1_from_ticks``）と一致する（別式を持ち込まない）。"""
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path)

    got = rebuild.authoritative_day_m1(_DAY, symbol=_TOKEN, data_dir=tmp_path)

    tick_m1.build_m1_from_ticks(
        _DAY, _DAY, symbol=_TOKEN, ref="whole", data_dir=tmp_path
    )
    whole = pd.read_csv(tick_m1.m1_csv_path(ref="whole", data_dir=tmp_path))
    assert list(got.index.strftime("%Y-%m-%d %H:%M:%S")) == list(whole["date"])


def test_the_authoritative_day_drops_the_phantom_bars(tmp_path):
    """裁定の要点: 権威経路では外れ分バーが落ちている（増分経路には無い判断）。"""
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path)

    got = rebuild.authoritative_day_m1(_DAY, symbol=_TOKEN, data_dir=tmp_path)

    assert list(got.index.strftime("%H:%M")) == [
        "09:00", "09:01", "09:02", "09:03", "09:06", "09:07", "09:08", "09:09"
    ]


# =====================================================================
# 外れ値日: 該当日区間を原子置換する
# =====================================================================

def test_a_day_with_outliers_is_replaced_by_the_authoritative_bars(tmp_path, store):
    """外れ値日は置換され、当日区間が全量経路と一致する（設計 §10 の要求）。"""
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path)

    outcome = rebuild.rebuild_day(_DAY, **store)

    tick_m1.build_m1_from_ticks(_DAY, _DAY, symbol=_TOKEN, ref="whole", data_dir=tmp_path)
    whole = pd.read_csv(tick_m1.m1_csv_path(ref="whole", data_dir=tmp_path))
    assert outcome == rebuild.REPLACED
    assert _m1_dates(tmp_path) == list(whole["date"])


def test_the_replacement_leaves_the_other_days_byte_identical(tmp_path, store):
    """当日**以外**の区間は 1 バイトも動かさない（再構築は日に閉じる）。"""
    prev_rows, prev_until = _rows_for(_PREV, minutes=6)
    _publish(_PREV, prev_rows, prev_until, tmp_path)
    prefix_before = tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).read_bytes()
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path)

    rebuild.rebuild_day(_DAY, **store)

    after = tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).read_bytes()
    assert after.startswith(prefix_before)


def test_rebuilding_twice_changes_nothing_the_second_time(tmp_path, store):
    """冪等: 一度是正した日は次から差分が無い（毎日書き直す常駐にしない）。"""
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path)
    rebuild.rebuild_day(_DAY, **store)
    settled = tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).read_bytes()

    outcome = rebuild.rebuild_day(_DAY, **store)

    assert outcome == rebuild.UNCHANGED
    assert tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).read_bytes() == settled


# =====================================================================
# 清浄日: 書込 0（CX-b と整合）
# =====================================================================

def test_a_clean_day_is_left_untouched(tmp_path, store):
    """清浄日は増分経路の出力がそのまま権威と一致するため、置換しない。"""
    rows, until = _rows_for(_DAY, minutes=8)
    _publish(_DAY, rows, until, tmp_path)
    before = tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).read_bytes()

    outcome = rebuild.rebuild_day(_DAY, **store)

    assert outcome == rebuild.UNCHANGED
    assert tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).read_bytes() == before


def test_a_clean_day_issues_no_write_at_all(tmp_path, store, monkeypatch):
    """CX-b: 清浄日は M1 追記も rollup 再生成も **1 回も発行しない**。

    ここで固定するのは回数ではなく**無駄の不在**である。差が無い日に 1 回でも書けば、
    それは「作ってから捨てる」書込であり、常駐の毎日の固定費になる。
    """
    rows, until = _rows_for(_DAY, minutes=8)
    _publish(_DAY, rows, until, tmp_path, update_rollups=True)
    writes = fakes.CallSpy(tick_m1.append_m1_rows)
    rollups = fakes.CallSpy(rollup.stream_build)
    monkeypatch.setattr(tick_m1, "append_m1_rows", writes)
    monkeypatch.setattr(rollup, "stream_build", rollups)

    rebuild.rebuild_day(_DAY, **store)

    assert (writes.count, rollups.count) == (0, 0)


def test_a_changed_day_issues_the_writes_only_once(tmp_path, store, monkeypatch):
    """CX: 是正が要る日は書くが、同じ日を再び処理しても発行は増えない（周期に比例しない）。"""
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path, update_rollups=True)
    writes = fakes.CallSpy(tick_m1.append_m1_rows)
    monkeypatch.setattr(tick_m1, "append_m1_rows", writes)

    rebuild.rebuild_day(_DAY, **store)
    after_first = writes.count
    rebuild.rebuild_day(_DAY, **store)
    rebuild.rebuild_day(_DAY, **store)

    assert writes.count == after_first
    assert after_first > 0


@pytest.mark.parametrize("stored_days", [2, 5])
def test_the_number_of_parquet_reads_does_not_grow_with_the_stored_days(
    tmp_path, store, monkeypatch, stored_days
):
    """CX: 読む確定 parquet は当日 1 個。保存済み日数（2 点）を変えても発行が増えない。"""
    for offset in range(stored_days):
        day = _DAY - dt.timedelta(days=stored_days - 1 - offset)
        rows, until = _rows_for(day, minutes=4, phantom_minutes=(1,) if day == _DAY else ())
        _publish(day, rows, until, tmp_path)
    reads = fakes.CallSpy(pd.read_parquet)
    monkeypatch.setattr(pd, "read_parquet", reads)

    rebuild.rebuild_day(_DAY, **store)

    assert reads.count == 1


# =====================================================================
# 派生ロールアップの是正
# =====================================================================

def test_the_rollups_stop_carrying_the_phantom_after_the_rebuild(tmp_path, store):
    """派生ロールアップの同日が是正される（外れ値が上位足の high/low に残らない）。"""
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path, update_rollups=True)
    path = rollup._rollup_path(m1_chain.rollup_dir(ref=_REF, data_dir=tmp_path), "5m", _REF)
    assert pd.read_csv(path)["low"].min() == pytest.approx(15105.0)

    rebuild.rebuild_day(_DAY, **store)

    assert pd.read_csv(path)["low"].min() > 60000.0


def test_the_rollups_are_not_touched_on_a_clean_day(tmp_path, store):
    """清浄日はロールアップも 1 バイトも書き換えない。"""
    rows, until = _rows_for(_DAY, minutes=8)
    _publish(_DAY, rows, until, tmp_path, update_rollups=True)
    path = rollup._rollup_path(m1_chain.rollup_dir(ref=_REF, data_dir=tmp_path), "5m", _REF)
    before = path.read_bytes()

    rebuild.rebuild_day(_DAY, **store)

    assert path.read_bytes() == before


# =====================================================================
# 素材が無い日（Fail-Stop ではなく「やることが無い」）
# =====================================================================

def test_a_csv_with_a_different_column_set_is_fail_stop_rather_than_spliced(tmp_path, store):
    """列構成が食い違う CSV へ当日区間を挿し込まない（黙って空欄の行を作らない）。

    連結してから整形すると、欠けている列は NaN になり、**当日以外の行**が空欄付きで書き直される。
    出力が壊れるより先に止める（Fail-Stop）。
    """
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path)
    path = tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path)
    trimmed = pd.read_csv(path).drop(columns=["up", "dn"])
    trimmed.to_csv(path, index=False)
    before = path.read_bytes()

    with pytest.raises(Mt5SupplyError):
        rebuild.rebuild_day(_DAY, **store)

    assert path.read_bytes() == before


def test_a_day_without_a_finalized_parquet_is_reported_as_missing(tmp_path, store):
    """確定 parquet が無い日は再構築しない（推測で当日を作らない）。"""
    rows, until = _rows_for(_DAY, minutes=4)
    m1_chain.append_m1_for_closed_minutes(
        rows, ref=_REF, data_dir=tmp_path, until=until.replace(tzinfo=dt.timezone.utc)
    )
    before = tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).read_bytes()

    outcome = rebuild.rebuild_day(_DAY, **store)

    assert outcome == rebuild.MISSING
    assert tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).read_bytes() == before


def test_a_ref_without_any_published_m1_is_reported_as_missing(tmp_path, store):
    """M1 CSV がまだ無いなら是正対象も無い（空 CSV を作らない）。"""
    rows, until = _rows_for(_DAY, minutes=4)
    journal.append(_DAY, rows, symbol=_TOKEN, data_dir=tmp_path)
    journal.finalize(_DAY, symbol=_TOKEN, data_dir=tmp_path)

    outcome = rebuild.rebuild_day(_DAY, **store)

    assert outcome == rebuild.MISSING
    assert not tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).exists()
