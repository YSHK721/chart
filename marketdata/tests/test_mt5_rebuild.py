"""UTC 日が閉じた後の M1 再構築の検定（ISSUE-447 段階 1 / 設計 §10 の裁定＝案 b）。

裁定の内容:
    日内増分の M1 には日次統計を要する外れ値除去（日内 close 中央値から ±30% 乖離する分バーの
    除去・ISSUE-107）が**原理的に適用できない**。数本のバーの中央値は日の中央値ではないからで
    ある。よって日中の M1 は暫定値として表示し、UTC 日が閉じた時点（確定 parquet が出来た後）で
    権威経路により当日を再計算し、**差分がある日だけ**該当日区間を原子置換する。

本検定が固定するのは次のとおりである:
    1. 再構築後の当日区間が全量経路（``tick_m1.build_m1_from_ticks``）と一致すること
       ＝「確定記録は既存権威と完全一致する」（設計 §10 の要求そのもの）
    2. 当日**以外**の区間を 1 バイトも動かさないこと
    3. 清浄日は**書込 0**（計算量検定 CX-b と整合。差が無いのに書けば、毎日全ファイルを
       書き直す常駐になる）
    4. 読む確定 parquet が当日 1 個だけであること（保存済み日数に比例して増えない）
    5. 構造: rebuild.py は外れ値除去 ``marketdata.outlier_policy.repair_day_outliers`` を
       import・属性参照・別名のどの形でも名指さない（外れ値除去の第 2 実装を持たない）
    6. 外れ値除去をかけた日 − 作り直す当日 = 0（計算量検定。保存日数 2 点・外れ分の有無の両方・
       ``authoritative_day_m1`` と ``rebuild_day`` の両方）
    7. M1 全体の読みは周期あたり 0・閉じた日 1 回まで（蓄積 2 点）／是正が要る日を再び処理しても
       書込が増えない（冪等）
    8. 派生ロールアップの同日も是正し、清浄日はロールアップも書き換えない
    9. 列構成が食い違う CSV は Fail-Stop・確定 parquet や M1 CSV が無い日は MISSING
"""
from __future__ import annotations

import ast
import datetime as dt
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from marketdata import outlier_policy, rollup, tick_m1
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


def _publish_days_ending_on(target: dt.date, stored_days: int, tmp_path, rows_of) -> None:
    """``target`` で終わる ``stored_days`` 日を古い日から順に公開する（各日の中身は ``rows_of(day)``）。

    保存済み日数を規模とする計算量検定が共有する唯一の日の並び（日ごとのティックは各検定が決める）。
    """
    for offset in range(stored_days):
        day = target - dt.timedelta(days=stored_days - 1 - offset)
        rows, until = rows_of(day)
        _publish(day, rows, until, tmp_path)


def _m1_dates(tmp_path, ref=_REF) -> "list[str]":
    return list(pd.read_csv(tick_m1.m1_csv_path(ref=ref, data_dir=tmp_path))["date"])


@pytest.fixture()
def store(tmp_path):
    return dict(symbol=_TOKEN, ref=_REF, data_dir=tmp_path)


# =====================================================================
# 権威一致: 再構築の入力は「全量経路が作る当日 M1」そのもの
# =====================================================================

def _identifiers_in(source: Path) -> "set[str]":
    """``source`` のコードに現れる名前の集合（Name・属性・import の名前と別名）。

    import の名前はドット区切りを分けて数える（ドット付きの import の途中の成分も拾う）。
    docstring とコメントはコードではないので数えない（説明文に規則の名前を書くのは自由）。
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.update(node.name.split("."))
            names.update([node.asname] if node.asname else [])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.update(node.module.split("."))
    return names


def test_rebuild_does_not_name_the_outlier_rule_in_any_form():
    """日次作り直しは外れ値除去を別に実装しない（素材化ごと tick_m1 の公開の口へ委ねる）。

    外れ値除去の唯一の実装 ``marketdata.outlier_policy.repair_day_outliers`` を、rebuild.py は
    import・属性参照（tick_m1 経由を含む）・別名のどの形でも名指さない。名指せば、公開の口
    ``marketdata.tick_m1.materialize_m1_day`` の出力に外れ値除去をもう一度かける第 2 実装を
    書けてしまう（依存表は import しか見ず、tick_m1 の属性経由の呼出しを落とせない）。
    """
    # Arrange
    source = Path(rebuild.__file__)

    # Act
    named = _identifiers_in(source)

    # Assert
    assert named & {"outlier_policy", "repair_day_outliers"} == set()


def test_the_authoritative_day_equals_what_the_whole_build_produces(tmp_path, store):
    """再計算の中身が権威（``build_m1_from_ticks``）と一致する（別式を持ち込まない）。"""
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path)

    got = rebuild.authoritative_day_m1(_DAY, symbol=_TOKEN, ref=_REF, data_dir=tmp_path)

    tick_m1.build_m1_from_ticks(
        _DAY, _DAY, symbol=_TOKEN, ref="whole", data_dir=tmp_path
    )
    whole = pd.read_csv(tick_m1.m1_csv_path(ref="whole", data_dir=tmp_path))
    assert list(got.index.strftime("%Y-%m-%d %H:%M:%S")) == list(whole["date"])


def test_the_authoritative_day_drops_the_phantom_bars(tmp_path):
    """裁定の要点: 権威経路では外れ分バーが落ちている（増分経路には無い判断）。"""
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path)

    got = rebuild.authoritative_day_m1(_DAY, symbol=_TOKEN, ref=_REF, data_dir=tmp_path)

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
    _publish_days_ending_on(
        _DAY, stored_days, tmp_path,
        lambda day: _rows_for(day, minutes=4, phantom_minutes=(1,) if day == _DAY else ()),
    )
    reads = fakes.CallSpy(pd.read_parquet)
    monkeypatch.setattr(pd, "read_parquet", reads)

    rebuild.rebuild_day(_DAY, **store)

    assert reads.count == 1


@pytest.mark.parametrize("stored_days", [5, 50])
def test_the_m1_read_is_zero_per_cycle_and_at_most_one_per_closed_day(
    tmp_path, store, monkeypatch, stored_days
):
    """CX: M1 全体の読みは**日 1 回まで**であり、周期あたりでは 0（蓄積 2 点で固定）。

    設計 §10 は「清浄日でも当日区間の比較のために M1 を読んでよい（1 日 1 回まで）」で確定して
    いる。よってここで固定するのは読み自体の禁止ではなく**上限**である:

    1. 日が閉じない周期は再構築を 1 回も発行しない（``rebuild_days(())`` = 常駐の通常運転）
    2. 日が閉じたときの読みは 1 日 1 回まで

    どちらも蓄積（5 日 / 50 日）で変わらない。周期あたりに 1 回でも読めば、それは常駐の
    固定費が M1 の大きさに比例して伸びるということであり、ISSUE-450 と同型になる。
    """
    _publish_days_ending_on(_DAY, stored_days, tmp_path, lambda day: _rows_for(day, minutes=2))
    reads = fakes.CallSpy(pd.read_csv)
    monkeypatch.setattr(pd, "read_csv", reads)

    rebuild.rebuild_days((), **store)
    per_cycle = reads.count
    rebuild.rebuild_day(_DAY, **store)

    assert per_cycle == 0
    assert reads.count - per_cycle == 1


def _days_of_the_cleaned_bars(*args, **kwargs) -> "tuple[dt.date, ...]":
    """外れ値除去 1 回が対象にした UTC 日（渡された分バーの日・昇順）。"""
    bars = args[0] if args else kwargs["df"]
    return tuple(sorted(set(pd.DatetimeIndex(bars.index).date)))


@pytest.mark.parametrize("entry", ["authoritative_day_m1", "rebuild_day"])
@pytest.mark.parametrize("phantom", [True, False], ids=["with-phantom", "clean"])
@pytest.mark.parametrize("stored_days", [2, 5])
def test_the_outlier_removal_is_issued_only_for_the_day_it_rebuilds(
    tmp_path, store, monkeypatch, entry, phantom, stored_days
):
    """CX（Y-B）: 外れ値除去をかけた日 − 作り直す当日 = 0（保存日数 2 点・外れ分の有無の両方）。

    ``marketdata.outlier_policy.repair_day_outliers`` に Test Spy を付け、除去 1 回ごとに対象の
    日を記録する。発行（除去をかけた日の多重集合）と使用（作り直す当日 1 日ぶん・入力の対象日から
    導く）の差を両向きで 0 と表明する:

    - 発行 − 使用 = 0: 他日を除去しない・同じ日に二重に除去しない（出力が変わらない浪費）
    - 使用 − 発行 = 0: 除去が抜けない（清浄日では出力が変わらないため状態検証では落ちない）

    除去の書き方（属性経由・別名・``getattr`` 等）に依らず、実行時に呼ばれた回数で落とす。
    継ぎ目: 呼出元 ``tick_m1._clean_m1_day`` は ``outlier_policy.repair_day_outliers`` を
    モジュール属性として呼出時に引くので、モジュール属性の差し替えが効く。
    """
    # Arrange
    target = _DAY
    _publish_days_ending_on(
        target, stored_days, tmp_path,
        lambda day: _rows_for(day, minutes=6, phantom_minutes=(2,) if phantom else ()),
    )
    spy = fakes.CallSpy(outlier_policy.repair_day_outliers, measure=_days_of_the_cleaned_bars)
    monkeypatch.setattr(outlier_policy, "repair_day_outliers", spy)
    used = Counter((day,) for day in (target,))

    # Act
    getattr(rebuild, entry)(target, **store)

    # Assert
    issued = Counter(spy.measurements)
    assert (issued - used, used - issued) == (Counter(), Counter())


# =====================================================================
# 派生ロールアップの是正
# =====================================================================

def test_the_rollups_stop_carrying_the_phantom_after_the_rebuild(tmp_path, store):
    """派生ロールアップの同日が是正される（外れ値が上位足の high/low に残らない）。"""
    rows, until = _rows_for(_DAY, minutes=10, phantom_minutes=(4, 5))
    _publish(_DAY, rows, until, tmp_path, update_rollups=True)
    path = rollup._rollup_path(m1_chain.rollup_dir(ref=_REF, data_dir=tmp_path), "5m", _REF)
    # ファントムの水準は bid そのもの（mid 時代の期待値は 15105.0＝bid + 半スプレッド 5.0）。
    assert pd.read_csv(path)["low"].min() == pytest.approx(15100.0)

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
