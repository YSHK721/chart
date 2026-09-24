"""供給の鮮度を兄弟系列の相対的な進みで判定する規則の検定（ISSUE-526 段 1）。

ここで固定するのは「暦を 1 行も持たずに、片方の系列だけが止まったことを言えるか」である。
時間を閾値にすると休場でも鳴る（そして鳴り止まないので誰も見なくなる）。基準となる兄弟系列が
進んだ観測区間だけを判定の対象にすれば、休場では基準も進まないため判定自体が起きない。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd

from marketdata import dataset_registry, supply_freshness

_HEADER = "date,open,high,low,close,volume,up,dn"


def _write_m1(data_dir, series: str, *, last_minute: str, rows: int = 3) -> None:
    """``<data_dir>/<series>_m1.csv`` を rows 行だけ書く（末尾が last_minute になる）。"""
    end = pd.Timestamp(last_minute)
    index = pd.date_range(end=end, periods=rows, freq="min")
    lines = [_HEADER] + [
        f"{ts:%Y-%m-%d %H:%M:%S},1,2,0.5,1.5,10,6,4" for ts in index
    ]
    (data_dir / f"{series}_m1.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


# =====================================================================
# 先端の読み口
# =====================================================================

def test_the_tip_of_a_series_is_the_date_of_its_last_m1_row(tmp_path):
    """系列 ref を渡すと、その M1 CSV の末尾 1 行の date が返る（naive UTC）。"""
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")

    tip = supply_freshness.m1_tip("jp225_mt5", data_dir=tmp_path)

    assert tip == pd.Timestamp("2026-09-24 22:26:00")


def test_the_tip_is_none_when_the_series_has_no_file_yet(tmp_path):
    """まだ 1 行も書かれていない系列の先端は None（無いことを無いと言う）。"""
    tip = supply_freshness.m1_tip("jp225_mt5", data_dir=tmp_path)

    assert tip is None


# =====================================================================
# 判定規則（同一銘柄の兄弟系列どうしの相対比較）
# =====================================================================

def test_both_series_are_healthy_when_both_advanced(tmp_path):
    """基準も対象も進んだ観測区間では、どちらも健全。"""
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread"), data_dir=tmp_path)
    watch.observe()

    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:31:00")
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:31:00")
    verdicts = watch.observe()

    assert verdicts == {
        "jp225_mt5": supply_freshness.HEALTHY,
        "jp225_mt5_spread": supply_freshness.HEALTHY,
    }


def test_a_series_is_stale_when_the_reference_advanced_and_it_did_not(tmp_path):
    """基準が進んだのに対象が 1 分も進んでいなければ異常（片側だけの停止）。"""
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread"), data_dir=tmp_path)
    watch.observe()

    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:31:00")
    verdicts = watch.observe()

    assert verdicts == {
        "jp225_mt5": supply_freshness.HEALTHY,
        "jp225_mt5_spread": supply_freshness.STALE,
    }


def test_nothing_rings_when_neither_series_advanced(tmp_path):
    """休場: どちらも進まない観測区間では鳴らない（暦を 1 行も持たない根拠）。"""
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread"), data_dir=tmp_path)
    watch.observe()

    verdicts = watch.observe()

    assert verdicts == {
        "jp225_mt5": supply_freshness.HEALTHY,
        "jp225_mt5_spread": supply_freshness.HEALTHY,
    }


def test_nothing_rings_while_the_reference_advanced_less_than_the_required_minutes(tmp_path):
    """基準の進みが k 分に満たない観測区間では鳴らない（読む順の 1 分差で騒がない）。"""
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread"),
        data_dir=tmp_path,
        min_reference_advance_minutes=3,
    )
    watch.observe()

    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:28:00")
    verdicts = watch.observe()

    assert verdicts == {
        "jp225_mt5": supply_freshness.HEALTHY,
        "jp225_mt5_spread": supply_freshness.HEALTHY,
    }


def test_it_rings_when_the_reference_advanced_exactly_the_required_minutes(tmp_path):
    """境界: 基準がちょうど k 分進んだ観測区間は判定の対象である（k 分ぶん＝以上）。"""
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread"),
        data_dir=tmp_path,
        min_reference_advance_minutes=3,
    )
    watch.observe()

    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:29:00")
    verdicts = watch.observe()

    assert verdicts == {
        "jp225_mt5": supply_freshness.HEALTHY,
        "jp225_mt5_spread": supply_freshness.STALE,
    }


def test_a_series_without_any_reference_is_undecidable(tmp_path):
    """基準が 1 本も無ければ判定不能（健全とは**別の値**・黙って合格にしない）。"""
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(("jp225_mt5",), data_dir=tmp_path)
    watch.observe()

    verdicts = watch.observe()

    assert verdicts == {"jp225_mt5": supply_freshness.UNDECIDABLE}
    assert supply_freshness.UNDECIDABLE != supply_freshness.HEALTHY


def test_a_series_of_another_symbol_is_not_used_as_a_reference(tmp_path):
    """基準になれるのは**同じ銘柄**の兄弟系列だけ（銘柄は台帳が引く）。

    別銘柄の系列が進んだことは、この系列が止まっている根拠にならない。
    """
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:26:00")
    _write_m1(tmp_path, "sample", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread", "sample"), data_dir=tmp_path)
    watch.observe()

    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:31:00")
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:31:00")
    verdicts = watch.observe()

    assert verdicts == {
        "jp225_mt5": supply_freshness.HEALTHY,
        "jp225_mt5_spread": supply_freshness.HEALTHY,
        "sample": supply_freshness.UNDECIDABLE,
    }


def test_a_series_whose_tip_cannot_be_read_is_undecidable(tmp_path):
    """対象の先端が読めない（ファイルがまだ無い）系列は判定不能。

    進みが測れていないものを、進んでいないとも健全とも言わない。
    """
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread"), data_dir=tmp_path)
    watch.observe()

    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:31:00")
    verdicts = watch.observe()

    assert verdicts == {
        "jp225_mt5": supply_freshness.UNDECIDABLE,
        "jp225_mt5_spread": supply_freshness.UNDECIDABLE,
    }


# =====================================================================
# 実データ（読み取りのみ・1 バイトも書かない）
# =====================================================================

def test_every_live_tick_series_reads_healthy_right_now():
    """いま供給されている 3 系列の先端を読み、すべて健全と出る（合格の目印）。

    観測を 2 回続けて取る（1 回目は前回観測が無いので判定不能になる）。連続 2 回の間に
    どの系列も止まっていないことを、兄弟どうしの相対比較だけで言う。
    """
    refs = sorted(dataset_registry.tick_refs())
    watch = supply_freshness.M1FreshnessWatch(refs)
    watch.observe()

    verdicts = watch.observe()

    assert {ref: supply_freshness.m1_tip(ref) is not None for ref in refs} == {
        ref: True for ref in refs
    }
    assert verdicts == {ref: supply_freshness.HEALTHY for ref in refs}
