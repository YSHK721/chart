"""鮮度（段 1）と書き手の在否（段 2）を束ねた報告の検定（ISSUE-526 段 3）。

片方だけでは言えないことがある。先端が進まないという事実は、書き手が居るか居ないかで意味が
正反対になる:

  - 書き手が在るのに進まない → 固まり（プロセスは生きている。上流か内部で詰まっている）
  - 正規の書き手が居ない     → 停止（起こし直す以外に進みようがない）
  - 在って進んでいる         → 健全

この 3 つを同じ「異常」へ潰すと、運用は毎回どちらなのかを手で調べ直すことになる。束ねる価値は
ここにあるので、**3 つが別の値であること**を固定する。

読み取りのみ。実データを見る検定も 1 バイトも書かない（錠も止めない・起動もしない）。PID は
起動ごとに変わるため値を焼き込まず、**この検定を走らせているプロセス自身**を「生きている
書き手」として使う。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import os

import pandas as pd

from marketdata import dataset_registry, supply_health

_HEADER = "date,open,high,low,close,volume,up,dn"

#: どのプロセスのコマンド行にも現れない語（不在を作るための期待値）。
_FOREIGN_PROGRAM = "supply_health_no_such_program"

#: 供給の名前（ベンダ）。どの系列がどちらの供給に属するかは台帳の vendor 欄が引く。
_DUKASCOPY = "dukascopy"
_MT5 = "mt5"


def _live_program() -> str:
    """自プロセスのコマンド行（在否の読み口が /proc から得るのと同じ形）。

    これを期待値に据えると、自プロセスが「生きている書き手」として在と読まれる。新しい
    プロセスを起こさずに在を作れるため、検定が常駐の起動・停止に触らない。
    """
    with open(f"/proc/{os.getpid()}/cmdline", "rb") as handle:
        return handle.read().replace(b"\0", b" ").decode("utf-8", "replace")


def _write_lock(data_dir, filename: str) -> None:
    """錠ファイルを書き手と同じ書式（保持者 PID と ISO 時刻の 1 行）で置く。"""
    (data_dir / filename).write_text(
        f"{os.getpid()} 2026-09-25T03:30:00.000000+00:00\n", encoding="utf-8")


def _write_m1(data_dir, ref: str, *, last_minute: str, rows: int = 3) -> None:
    """ref の M1 CSV を rows 行だけ書く（末尾が last_minute になる）。

    置き場の名前は台帳が引く（ref 名から組む写しを検定側に持たない）。
    """
    end = pd.Timestamp(last_minute)
    index = pd.date_range(end=end, periods=rows, freq="min")
    lines = [_HEADER] + [
        f"{ts:%Y-%m-%d %H:%M:%S},1,2,0.5,1.5,10,6,4" for ts in index
    ]
    series = dataset_registry.series_of(ref)
    (data_dir / f"{series}_m1.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _supplies(*, mt5_program: str, dukascopy_program: str) -> dict:
    """検定用の供給台帳（錠の名前と、その錠を持つはずのプログラム）。"""
    return {
        _DUKASCOPY: supply_health.Supply(
            lock_filename="dukascopy_writer.lock", program=dukascopy_program),
        _MT5: supply_health.Supply(
            lock_filename="mt5_writer.lock", program=mt5_program),
    }


def _arrange_both_present(data_dir, *, mt5_program=None):
    """2 本の書き手が在る状態を作り、1 回目の観測を済ませた watch を返す。

    1 回目の観測は前回観測を持たないため進みが測れない（判定不能）。相対比較で物を言うには
    2 回目が要る。
    """
    supplies = _supplies(
        mt5_program=mt5_program or _live_program(),
        dukascopy_program=_live_program(),
    )
    for supply in supplies.values():
        _write_lock(data_dir, supply.lock_filename)
    for ref in dataset_registry.tick_refs():
        _write_m1(data_dir, ref, last_minute="2026-09-24 22:26:00")
    watch = supply_health.SupplyHealthWatch(supplies=supplies, data_dir=data_dir)
    watch.observe()
    return watch


def _advance(data_dir, refs, *, last_minute: str) -> None:
    """refs の先端を last_minute まで進める（渡さなかった系列は据え置き＝進まない）。"""
    for ref in refs:
        _write_m1(data_dir, ref, last_minute=last_minute)


def _refs_of(vendor: str):
    """その供給が書いている系列（台帳の vendor 欄で引く）。"""
    return tuple(
        ref for ref in dataset_registry.tick_refs()
        if dataset_registry.REGISTRY[ref].vendor == vendor
    )


# =====================================================================
# 3 つの区別（本段の価値）
# =====================================================================

def test_a_present_writer_whose_series_stop_advancing_reads_as_stuck(tmp_path):
    """在るのに進まない＝固まり。同じ観測で、進んでいる側は健全と出る。"""
    # Arrange: 2 本とも在。dukascopy 側だけ 5 分進め、mt5 側は据え置く。
    watch = _arrange_both_present(tmp_path)
    _advance(tmp_path, _refs_of(_DUKASCOPY), last_minute="2026-09-24 22:31:00")

    # Act
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        _DUKASCOPY: supply_health.HEALTHY,
        _MT5: supply_health.STUCK,
    }


def test_an_absent_writer_whose_series_stop_advancing_reads_as_stopped(tmp_path):
    """不在で進まない＝停止。固まりとは**別の値**である（次の一手が違う）。"""
    # Arrange: mt5 の錠が名指すのは、どのプロセスでもないプログラム＝不在。
    watch = _arrange_both_present(tmp_path, mt5_program=_FOREIGN_PROGRAM)
    _advance(tmp_path, _refs_of(_DUKASCOPY), last_minute="2026-09-24 22:31:00")

    # Act
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        _DUKASCOPY: supply_health.HEALTHY,
        _MT5: supply_health.STOPPED,
    }


def test_both_writers_present_and_advancing_read_as_healthy(tmp_path):
    """在って進んでいる＝健全（異常の値と取り違えない）。"""
    # Arrange
    watch = _arrange_both_present(tmp_path)
    _advance(tmp_path, dataset_registry.tick_refs(), last_minute="2026-09-24 22:31:00")

    # Act
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        _DUKASCOPY: supply_health.HEALTHY,
        _MT5: supply_health.HEALTHY,
    }


def test_the_three_distinctions_are_three_different_values():
    """固まり・停止・健全は 3 つの別の値（束ねても区別が潰れていない）。"""
    # Arrange / Act
    values = (supply_health.HEALTHY, supply_health.STUCK, supply_health.STOPPED)

    # Assert
    assert len(set(values)) == 3


def test_an_absent_writer_reads_as_stopped_even_while_the_series_advance(tmp_path):
    """不在は進みより強い証拠である（正規の書き手が居ないなら、進んでいても停止と呼ぶ）。

    在否は 1 回の観測で言えるが、進みは 2 回を要する。加えて、錠を持たない誰かが書いて
    先端が伸びている状態は ISSUE-488 の危険そのもの（黙って健全にしてはならない）。
    """
    # Arrange
    watch = _arrange_both_present(tmp_path, mt5_program=_FOREIGN_PROGRAM)
    _advance(tmp_path, dataset_registry.tick_refs(), last_minute="2026-09-24 22:31:00")

    # Act
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        _DUKASCOPY: supply_health.HEALTHY,
        _MT5: supply_health.STOPPED,
    }


# =====================================================================
# 分からないことを黙って合格にしない
# =====================================================================

def test_the_first_observation_is_undecidable_because_no_advance_can_be_measured(tmp_path):
    """1 回目の観測は進みが測れない＝判定不能（健全とは別の値）。"""
    # Arrange
    supplies = _supplies(mt5_program=_live_program(), dukascopy_program=_live_program())
    for supply in supplies.values():
        _write_lock(tmp_path, supply.lock_filename)
    for ref in dataset_registry.tick_refs():
        _write_m1(tmp_path, ref, last_minute="2026-09-24 22:26:00")
    watch = supply_health.SupplyHealthWatch(supplies=supplies, data_dir=tmp_path)

    # Act
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        _DUKASCOPY: supply_health.UNDECIDABLE,
        _MT5: supply_health.UNDECIDABLE,
    }


def test_a_missing_lock_file_is_undecidable_not_stopped(tmp_path):
    """錠ファイルが無いのは「居ない」ではなく「分からない」（別の値のまま束ねる）。"""
    # Arrange: mt5 の錠だけ置かない。系列はどちらも進める。
    supplies = _supplies(mt5_program=_live_program(), dukascopy_program=_live_program())
    _write_lock(tmp_path, supplies[_DUKASCOPY].lock_filename)
    for ref in dataset_registry.tick_refs():
        _write_m1(tmp_path, ref, last_minute="2026-09-24 22:26:00")
    watch = supply_health.SupplyHealthWatch(supplies=supplies, data_dir=tmp_path)
    watch.observe()
    _advance(tmp_path, dataset_registry.tick_refs(), last_minute="2026-09-24 22:31:00")

    # Act
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        _DUKASCOPY: supply_health.HEALTHY,
        _MT5: supply_health.UNDECIDABLE,
    }


# =====================================================================
# 総合判定と報告の形（プロセス外から読む側の契約）
# =====================================================================

def test_the_overall_verdict_is_the_alarm_when_any_supply_alarms():
    """1 本でも異常なら総合は異常（健全な側に隠れない）。"""
    # Arrange
    verdicts = {_DUKASCOPY: supply_health.HEALTHY, _MT5: supply_health.STOPPED}

    # Act
    overall = supply_health.overall(verdicts)

    # Assert
    assert overall == supply_health.STOPPED


def test_the_overall_verdict_is_healthy_only_when_every_supply_is_healthy():
    """全部健全なら総合も健全（判定不能が 1 つでも混ざれば健全と言わない）。"""
    # Arrange
    all_healthy = {_DUKASCOPY: supply_health.HEALTHY, _MT5: supply_health.HEALTHY}
    one_unknown = {_DUKASCOPY: supply_health.HEALTHY, _MT5: supply_health.UNDECIDABLE}

    # Act
    verdicts = (supply_health.overall(all_healthy), supply_health.overall(one_unknown))

    # Assert
    assert verdicts == (supply_health.HEALTHY, supply_health.UNDECIDABLE)


def test_the_report_puts_the_overall_verdict_on_the_first_line(tmp_path):
    """報告の 1 行目は総合判定（起動スクリプトは 1 行目だけ読めば済む）。"""
    # Arrange
    watch = _arrange_both_present(tmp_path, mt5_program=_FOREIGN_PROGRAM)
    _advance(tmp_path, _refs_of(_DUKASCOPY), last_minute="2026-09-24 22:31:00")

    # Act
    lines = watch.report().splitlines()

    # Assert
    assert lines[0] == supply_health.STOPPED


def test_the_report_names_every_supply_with_its_own_verdict(tmp_path):
    """2 行目以降は供給ごとに「名前 判定」の 1 行（機械可読・どれが異常かが読める）。"""
    # Arrange
    watch = _arrange_both_present(tmp_path, mt5_program=_FOREIGN_PROGRAM)
    _advance(tmp_path, _refs_of(_DUKASCOPY), last_minute="2026-09-24 22:31:00")

    # Act
    rows = [line.split() for line in watch.report().splitlines()[1:]]

    # Assert
    assert dict((name, verdict) for name, verdict in rows) == {
        _DUKASCOPY: supply_health.HEALTHY,
        _MT5: supply_health.STOPPED,
    }


def test_the_alarm_verdicts_are_exactly_the_two_abnormal_ones():
    """告知の対象は固まりと停止だけ（判定不能で鳴らすと、起動のたびに鳴る）。"""
    # Arrange / Act
    alarms = supply_health.ALARM_VERDICTS

    # Assert
    assert set(alarms) == {supply_health.STUCK, supply_health.STOPPED}


# =====================================================================
# 台帳（観測の取り残しを作らない）
# =====================================================================

def test_every_tick_series_belongs_to_exactly_one_shipped_supply():
    """ティック由来の全系列が、出荷台帳のどれか 1 つの供給に属する。

    属さない系列があれば、その系列は誰にも観測されないまま静かに欠測する（ISSUE-526 そのもの）。
    """
    # Arrange
    vendors = set(supply_health.SUPPLIES)

    # Act
    unowned = sorted(
        ref for ref in dataset_registry.tick_refs()
        if dataset_registry.REGISTRY[ref].vendor not in vendors
    )

    # Assert
    assert unowned == []


# =====================================================================
# 実データ（読み取りのみ・1 バイトも書かない）
# =====================================================================

def test_both_running_supplies_read_as_healthy_right_now():
    """いま稼働している 2 本の供給が、束ねた報告でも健全と出る（合格の目印）。

    観測を 2 回続けて取る（1 回目は前回観測が無いので判定不能になる）。
    """
    # Arrange
    watch = supply_health.SupplyHealthWatch()
    watch.observe()

    # Act
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        name: supply_health.HEALTHY for name in supply_health.SUPPLIES
    }
