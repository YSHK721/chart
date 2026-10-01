"""1 つのティック列を複数の系列へ配る供給経路（ISSUE-511 段階 8-D-2b の段 3）。

用語（初出定義）:
    系列の組（refs）
        ＝ 同じティック木から導く datasetRef の集合。案内（``marketdata.tick_m1.series_plan``）が
          価格基準と spread 宣言を照合して 1 つにまとめる。
    先端（settled）
        ＝ その系列の既存 M1 CSV の最終 date（``marketdata.tick_m1.last_m1_date``）。系列ごとに
          違いうる（片方だけ先に書けた周期がある）。
    行選択（設計 D-1）
        ＝ 畳みへ渡す行を選ぶ規則。下限は ``min(先端)``（先端の無い系列が 1 つでもあれば下限なし）で
          **1 回だけ**選び、各系列へは ``index > その系列の先端`` の分バーだけを追記する。
    形成中の分（pending）
        ＝ ``until`` 以降の、まだ閉じていない分のティック。系列に依らないので**組に 1 本**である。
    発行 / 使用
        発行 ＝ 唯一の畳み点 ``marketdata.tick_m1._fold_ticks`` へ渡ったティック行数。
          使用 ＝ 出力（どれかの系列の M1 行）になったティック行数。規約の形は「発行 − 使用 = 0」
          （絶対命令 2026-08-28）。

本検定が固定するもの:
  I-1〜I-3 **挙動不変**: 既存の 1 系列の口（``append_m1_for_closed_minutes`` /
      ``usecases.PublishDataset`` / ``rebuild.rebuild_day``）は名前も戻り値も変わらず、1 要素を
      渡した系列版と**返り値も出力 CSV の byte も**一致する（Composition Root は段 5 まで 1 要素
      しか渡さない＝本段で実行時の挙動は 1 ビットも変わらない）。
  C-1 **畳みは 1 回**: 系列を 2 つ渡しても、発行 − 使用 = 0（系列ごとに畳み直さない）。
  C-2 **計算量**: 系列数 1 → 2 で畳みの発行もティック読取も**増えない**（規模 2 点で固定）。
  C-3 日次再構築でも系列数 1 → 2 で parquet 読取・畳みが増えない（規模 2 点）。
  D-1 **先端差**: 各系列は自分の先端より後の分だけを受け取り、畳んだ分は**必ずどれかの系列が
      使う**（下限が ``min(先端)`` であることの表明＝捨てる計算 0）。
  P-1 **pending は 1 本**: 系列数を増やしても形成中の分は増えない（中身も同一）。

本検定が固定しないもの（射程の明示）:
  - Composition Root（``tools/mt5_tick_watch.py``）の結線は段 5 の担当であり、本ファイルは触らない。
  - 台帳への 2 系列の宣言・データの穴埋めは段 4 以降の担当。本ファイルは合成 ref を一時登録して測る。
  - 案内そのものの Fail-Stop（基準・宣言の食い違い）は
    ``marketdata/tests/test_tick_m1_series_plan.py`` の F-1 が持つ。

回数そのもの（N 回）は期待値に焼き込まない。固定するのは無駄の不在である。
書込はすべて ``tmp_path``（data_dir を必ず渡す）。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import ingest, m1_chain, rebuild, usecases
from marketdata.mt5_ticks.fakes import FixedClock
from spread_series_fixture import (
    SNAPSHOT_PAIR,
    cleared_point_cache,  # noqa: F401  （import した先で autouse になる共有 fixture）
    register_tick_ref,
)

#: 合成 ref（spread 列を持つ / 持たない）。同じ価格基準で台帳へ一時登録する。
_SPREAD_REF = "zz_fan_spread"
_PLAIN_REF = "zz_fan_plain"
#: tick 木の枝（テスト専用・tmp_path の中だけ）。
_TREE = "FAN225"
_DAY = dt.date(2026, 9, 1)
_T0 = dt.datetime(2026, 9, 1, 9, 0)


def _register_pair(monkeypatch, tmp_path: Path) -> None:
    """spread 付きと spread 無しの 2 系列を、同じ価格基準で台帳へ一時登録する。"""
    register_tick_ref(monkeypatch, tmp_path, _SPREAD_REF, SNAPSHOT_PAIR)
    register_tick_ref(monkeypatch, tmp_path, _PLAIN_REF, None)


def _label_ms(when: dt.datetime) -> int:
    """ブローカーが名乗るラベル（UTC+3）のミリ秒（受信経路と同じ形）。"""
    return int(when.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + 3 * 3600 * 1000


def _rows(minutes: int, *, per_minute: int = 6, start: dt.datetime = _T0):
    """``start`` から ``minutes`` 分 × ``per_minute`` 本のティック（値は分ごと・本ごとに動く）。"""
    out = []
    for m in range(minutes):
        for i in range(per_minute):
            when = start + dt.timedelta(minutes=m, seconds=i * (60 // per_minute))
            bid = 66000.0 + m * 2.0 - i * 0.3
            out.append((_label_ms(when), bid, bid + 7.0 + (m % 3) * 0.5 + i * 0.1))
    return out


def _utc(when: dt.datetime) -> dt.datetime:
    return when.replace(tzinfo=dt.timezone.utc)


def _dates_of(path: Path) -> "set[pd.Timestamp]":
    """出力 M1 CSV に入っている分（date 列）の集合。"""
    if not path.is_file():
        return set()
    return set(pd.to_datetime(pd.read_csv(path)["date"]))


def _spy_folds(monkeypatch) -> "list[int]":
    """唯一の畳み点 ``tick_m1._fold_ticks`` を包み、畳んだティック行数を記録する。

    公開の口の入口ではなく共通前段を数える（入口で数えると、委譲先の内部で 2 回畳む変異が
    1 回に見えて素通りする・``test_mt5_rebuild_materialize.py`` のレビュー指摘 Y-1）。
    """
    folded: "list[int]" = []
    real = tick_m1._fold_ticks

    def spy(ticks, *args, **kwargs):
        folded.append(len(ticks))
        return real(ticks, *args, **kwargs)

    monkeypatch.setattr(tick_m1, "_fold_ticks", spy)
    return folded


def _spy_tick_reads(monkeypatch) -> "list[int]":
    """ティック行 → frame の整形（``ingest.rows_to_frame``）を包み、読んだ行数を記録する。"""
    reads: "list[int]" = []
    real = ingest.rows_to_frame

    def spy(rows):
        reads.append(len(rows))
        return real(rows)

    monkeypatch.setattr(ingest, "rows_to_frame", spy)
    return reads


def _spy_parquet_reads(monkeypatch) -> "list[object]":
    """確定 parquet の読取（``pd.read_parquet``）を包み、発行を記録する。"""
    reads: "list[object]" = []
    real = pd.read_parquet

    def spy(*args, **kwargs):
        reads.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", spy)
    return reads


def _put_parquet(data_dir: Path, day: dt.date, *, minutes: int, per_minute: int = 6) -> int:
    """``day`` の確定 parquet を tick 木へ置き、置いたティック数を返す。"""
    start = dt.datetime(day.year, day.month, day.day, 9, 0)
    stamps, bids, asks = [], [], []
    for m in range(minutes):
        for i in range(per_minute):
            bid = 66000.0 + m * 2.0 - i * 0.3
            stamps.append(start + dt.timedelta(minutes=m, seconds=i * (60 // per_minute)))
            bids.append(bid)
            asks.append(bid + 7.0 + (m % 3) * 0.5 + i * 0.1)
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime(stamps).tz_localize("UTC"),
        "bidPrice": bids, "askPrice": asks,
    })
    path = tick_m1.day_parquet_path(day, symbol=_TREE, data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path)
    return len(frame)


# =====================================================================
# I-1〜I-3 挙動不変（既存の 1 系列の口は 1 要素の薄い包み）
# =====================================================================

def test_i1_the_single_series_entry_equals_a_one_element_fan_out(tmp_path, monkeypatch):
    """I-1: 既存の口と 1 要素の系列版が、返り値も出力 CSV の byte も一致する。"""
    # Arrange: 同じ入力を 2 つの作業ディレクトリへ通す（互いの出力が混ざらない）。
    left, right = tmp_path / "single", tmp_path / "fan"
    left.mkdir()
    right.mkdir()
    rows = _rows(5)
    until = _utc(_T0 + dt.timedelta(minutes=4))

    # Act
    register_tick_ref(monkeypatch, left, _PLAIN_REF, None)
    single = m1_chain.append_m1_for_closed_minutes(
        rows, ref=_PLAIN_REF, data_dir=left, until=until
    )
    register_tick_ref(monkeypatch, right, _PLAIN_REF, None)
    fanned = m1_chain.append_m1_for_closed_minutes_for_series(
        rows, refs=(_PLAIN_REF,), data_dir=right, until=until
    )

    # Assert
    left_csv = tick_m1.m1_csv_path(ref=_PLAIN_REF, data_dir=left)
    right_csv = tick_m1.m1_csv_path(ref=_PLAIN_REF, data_dir=right)
    assert single.bars > 0 and left_csv.is_file()          # 空振り防止
    assert fanned.bars == {_PLAIN_REF: single.bars}
    assert fanned.pending_rows == single.pending_rows
    assert right_csv.read_bytes() == left_csv.read_bytes()


def test_i2_publishing_one_series_equals_publishing_a_one_element_set(tmp_path, monkeypatch):
    """I-2: ``PublishDataset`` と 1 要素の ``PublishSeries`` が、返り値も出力の byte も一致する。"""
    # Arrange
    left, right = tmp_path / "single", tmp_path / "fan"
    left.mkdir()
    right.mkdir()
    rows = _rows(5)
    clock = FixedClock(_utc(_T0 + dt.timedelta(minutes=4, seconds=30)))

    # Act
    register_tick_ref(monkeypatch, left, _PLAIN_REF, None)
    single = usecases.PublishDataset(ref=_PLAIN_REF, data_dir=left, clock=clock)(rows)
    register_tick_ref(monkeypatch, right, _PLAIN_REF, None)
    fanned = usecases.PublishSeries(refs=(_PLAIN_REF,), data_dir=right, clock=clock)(rows)

    # Assert: M1 も上位足もロールアップ状態も byte 一致。
    produced = {
        str(p.relative_to(left)): p.read_bytes()
        for p in sorted(left.rglob("*")) if p.is_file()
    }
    mirrored = {
        str(p.relative_to(right)): p.read_bytes()
        for p in sorted(right.rglob("*")) if p.is_file()
    }
    assert single.bars > 0 and len(produced) > 1           # 空振り防止（rollup も出ている）
    assert fanned.bars == {_PLAIN_REF: single.bars}
    assert fanned.pending_rows == single.pending_rows
    assert mirrored == produced


def test_i3_rebuilding_one_series_equals_rebuilding_a_one_element_set(tmp_path, monkeypatch):
    """I-3: ``rebuild_day`` と 1 要素の ``rebuild_day_for_series`` が、判定も出力の byte も一致する。"""
    # Arrange: 権威で CSV を作ってから当日区間を作り直す（差が無ければ UNCHANGED）。
    left, right = tmp_path / "single", tmp_path / "fan"
    for root in (left, right):
        root.mkdir()
        register_tick_ref(monkeypatch, root, _PLAIN_REF, None)
        _put_parquet(root, _DAY, minutes=6)
        tick_m1.build_m1_from_ticks(
            _DAY, _DAY, symbol=_TREE, ref=_PLAIN_REF, data_dir=root
        )
    left_csv = tick_m1.m1_csv_path(ref=_PLAIN_REF, data_dir=left)
    right_csv = tick_m1.m1_csv_path(ref=_PLAIN_REF, data_dir=right)

    # Act
    single = rebuild.rebuild_day(
        _DAY, symbol=_TREE, ref=_PLAIN_REF, data_dir=left, update_rollups=False
    )
    fanned = rebuild.rebuild_day_for_series(
        _DAY, symbol=_TREE, refs=(_PLAIN_REF,), data_dir=right, update_rollups=False
    )[_PLAIN_REF]

    # Assert
    assert single in {rebuild.UNCHANGED, rebuild.REPLACED}   # 空振り防止
    assert fanned == single
    assert right_csv.read_bytes() == left_csv.read_bytes()


# =====================================================================
# C-1 / C-2 畳みは 1 回・系列数を増やしても発行が増えない
# =====================================================================

@pytest.mark.parametrize("closed_minutes", [2, 20])
def test_c1_two_series_fold_the_ticks_only_once(tmp_path, monkeypatch, closed_minutes):
    """C-1: 2 系列へ配っても、畳みへ渡った行 − 出力に使った行 = 0（系列ごとに畳み直さない）。"""
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    per_minute = 6
    rows = _rows(closed_minutes + 1, per_minute=per_minute)
    until = _utc(_T0 + dt.timedelta(minutes=closed_minutes))
    folded = _spy_folds(monkeypatch)

    # Act
    got = m1_chain.append_m1_for_closed_minutes_for_series(
        rows, refs=(_SPREAD_REF, _PLAIN_REF), data_dir=tmp_path, until=until
    )

    # Assert: 使用＝閉じた分のティック行数（2 系列とも同じ畳みの結果を受け取る）。
    used = closed_minutes * per_minute
    assert got.bars == {_SPREAD_REF: closed_minutes, _PLAIN_REF: closed_minutes}
    assert "spread" in pd.read_csv(
        tick_m1.m1_csv_path(ref=_SPREAD_REF, data_dir=tmp_path), nrows=0
    ).columns                                              # 空振り防止（列形は系列ごと）
    assert "spread" not in pd.read_csv(
        tick_m1.m1_csv_path(ref=_PLAIN_REF, data_dir=tmp_path), nrows=0
    ).columns
    assert sum(folded) > 0                                 # 生存確認（Spy が数えた発行は 0 件でない）
    assert sum(folded) - used == 0, f"畳み {folded} − 使用 {used} ≠ 0（系列ごとに畳んでいます）"


def test_c2_going_from_one_series_to_two_adds_no_fold_and_no_tick_read(tmp_path, monkeypatch):
    """C-2: 系列数 1 → 2 で畳みの発行もティック読取も増えない（規模 2 点で固定）。

    回数そのものは期待値にしない。固定するのは「系列を足しても発行が増えないこと」と
    「発行 − 使用 = 0」である。
    """
    # Arrange
    issued: "dict[tuple[int, int], tuple[int, int, int]]" = {}
    per_minute = 6

    for closed_minutes in (2, 20):
        for n_series in (1, 2):
            root = tmp_path / f"m{closed_minutes}s{n_series}"
            root.mkdir()
            register_tick_ref(monkeypatch, root, _SPREAD_REF, SNAPSHOT_PAIR)
            register_tick_ref(monkeypatch, root, _PLAIN_REF, None)
            refs = (_SPREAD_REF, _PLAIN_REF)[:n_series]
            rows = _rows(closed_minutes + 1, per_minute=per_minute)
            folded = _spy_folds(monkeypatch)
            reads = _spy_tick_reads(monkeypatch)

            # Act
            got = m1_chain.append_m1_for_closed_minutes_for_series(
                rows, refs=refs, data_dir=root,
                until=_utc(_T0 + dt.timedelta(minutes=closed_minutes)),
            )

            # Assert（各点）: 発行 − 使用 = 0。
            used = closed_minutes * per_minute
            assert set(got.bars) == set(refs)
            assert sum(folded) - used == 0, (
                f"分 {closed_minutes}・系列 {n_series}: 畳み {sum(folded)} − 使用 {used} ≠ 0"
            )
            assert sum(reads) - used == 0, (
                f"分 {closed_minutes}・系列 {n_series}: 読取 {sum(reads)} − 使用 {used} ≠ 0"
            )
            issued[(closed_minutes, n_series)] = (len(folded), sum(folded), sum(reads))
            monkeypatch.undo()

    # Assert（2 点とも）: 系列を 1 → 2 にしても発行は 1 つも増えない。
    for closed_minutes in (2, 20):
        assert issued[(closed_minutes, 1)][0] > 0          # 生存確認（発行 0 件ではない）
        assert issued[(closed_minutes, 2)] == issued[(closed_minutes, 1)], (
            f"分 {closed_minutes}: 系列を 2 つにしたら発行が"
            f" {issued[(closed_minutes, 1)]} → {issued[(closed_minutes, 2)]} へ増えました"
        )


@pytest.mark.parametrize("minutes", [4, 12])
def test_c3_rebuilding_two_series_reads_the_day_once_and_folds_once(tmp_path, monkeypatch, minutes):
    """C-3: 日次再構築でも系列数 1 → 2 で parquet 読取・畳みが増えない（規模 2 点）。"""
    # Arrange
    issued = {}
    for n_series in (1, 2):
        root = tmp_path / f"m{minutes}s{n_series}"
        root.mkdir()
        register_tick_ref(monkeypatch, root, _SPREAD_REF, SNAPSHOT_PAIR)
        register_tick_ref(monkeypatch, root, _PLAIN_REF, None)
        refs = (_SPREAD_REF, _PLAIN_REF)[:n_series]
        ticks = _put_parquet(root, _DAY, minutes=minutes)
        for ref in refs:
            tick_m1.build_m1_from_ticks(_DAY, _DAY, symbol=_TREE, ref=ref, data_dir=root)
        folded = _spy_folds(monkeypatch)
        parquet_reads = _spy_parquet_reads(monkeypatch)

        # Act
        outcome = rebuild.rebuild_day_for_series(
            _DAY, symbol=_TREE, refs=refs, data_dir=root, update_rollups=False
        )

        # Assert（各点）: 当日ティックを 1 回だけ読み、1 回だけ畳む。
        assert outcome == {ref: rebuild.UNCHANGED for ref in refs}
        assert sum(folded) - ticks == 0, (
            f"系列 {n_series}: 畳み {sum(folded)} − 当日ティック {ticks} ≠ 0"
        )
        issued[n_series] = (len(folded), sum(folded), len(parquet_reads))
        monkeypatch.undo()

    # Assert: 系列を 1 → 2 にしても読取も畳みも増えない。
    assert issued[1][0] > 0 and issued[1][2] > 0           # 生存確認（発行 0 件ではない）
    assert issued[2] == issued[1], (
        f"系列を 2 つにしたら発行が {issued[1]} → {issued[2]} へ増えました"
    )


# =====================================================================
# D-1 先端差（各系列は自分の先端より後の分だけを受け取る）
# =====================================================================

def test_d1_each_series_receives_only_the_minutes_after_its_own_settled_edge(
    tmp_path, monkeypatch
):
    """D-1: 先端がずれた 2 系列へ、それぞれ自分の先端より後の分だけが入る。

    畳みへ渡す行の下限は ``min(先端)`` で**1 回だけ**選ぶ。先端が進んでいる側で落ちる分は、
    遅れている側が必ず使う＝畳んだ分に「どの系列も使わない分」は無い。
    """
    # Arrange: 09:00〜09:01 を両系列へ、09:02 を spread 側だけへ入れて先端をずらす。
    _register_pair(monkeypatch, tmp_path)
    m1_chain.append_m1_for_closed_minutes_for_series(
        _rows(2), refs=(_SPREAD_REF, _PLAIN_REF), data_dir=tmp_path,
        until=_utc(_T0 + dt.timedelta(minutes=2)),
    )
    m1_chain.append_m1_for_closed_minutes(
        _rows(1, start=_T0 + dt.timedelta(minutes=2)), ref=_SPREAD_REF, data_dir=tmp_path,
        until=_utc(_T0 + dt.timedelta(minutes=3)),
    )
    spread_path = tick_m1.m1_csv_path(ref=_SPREAD_REF, data_dir=tmp_path)
    plain_path = tick_m1.m1_csv_path(ref=_PLAIN_REF, data_dir=tmp_path)
    before = {_SPREAD_REF: _dates_of(spread_path), _PLAIN_REF: _dates_of(plain_path)}
    folded = _spy_folds(monkeypatch)

    # Act: 09:02〜09:04 のティックを組で渡す（09:05 以降は形成中）。
    got = m1_chain.append_m1_for_closed_minutes_for_series(
        _rows(3, start=_T0 + dt.timedelta(minutes=2)),
        refs=(_SPREAD_REF, _PLAIN_REF), data_dir=tmp_path,
        until=_utc(_T0 + dt.timedelta(minutes=5)),
    )

    # Assert
    minute = lambda k: pd.Timestamp(_T0 + dt.timedelta(minutes=k))  # noqa: E731
    added = {
        _SPREAD_REF: _dates_of(spread_path) - before[_SPREAD_REF],
        _PLAIN_REF: _dates_of(plain_path) - before[_PLAIN_REF],
    }
    assert before[_SPREAD_REF] != before[_PLAIN_REF]       # 前提（先端が実際にずれている）
    assert added[_SPREAD_REF] == {minute(3), minute(4)}
    assert added[_PLAIN_REF] == {minute(2), minute(3), minute(4)}
    assert got.bars == {_SPREAD_REF: 2, _PLAIN_REF: 3}
    # 畳んだ分は必ずどれかの系列が使う（使われない分＝捨てる計算が 0）。
    assert sum(folded) > 0                                 # 生存確認
    assert sum(folded) - 3 * 6 == 0, f"畳み {folded} に、どの系列も使わない行があります"
    assert added[_SPREAD_REF] | added[_PLAIN_REF] == {minute(2), minute(3), minute(4)}


# =====================================================================
# P-1 pending は組に 1 本
# =====================================================================

@pytest.mark.parametrize("n_series", [1, 2])
def test_p1_the_forming_minute_is_carried_once_regardless_of_the_number_of_series(
    tmp_path, monkeypatch, n_series
):
    """P-1: 形成中の分の持ち越しは系列数に依らず 1 本（系列ごとに複製しない）。"""
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    refs = (_SPREAD_REF, _PLAIN_REF)[:n_series]
    per_minute = 6
    rows = _rows(4, per_minute=per_minute)

    # Act
    got = m1_chain.append_m1_for_closed_minutes_for_series(
        rows, refs=refs, data_dir=tmp_path, until=_utc(_T0 + dt.timedelta(minutes=3))
    )

    # Assert: 形成中の分のティックちょうど（= 最後の 1 分ぶん）で、系列数に依らない。
    assert got.pending_rows == rows[-per_minute:]
    assert len(got.pending_rows) == per_minute


def test_p1_publishing_two_series_carries_one_pending_list(tmp_path, monkeypatch):
    """P-1: ``PublishSeries`` でも持ち越しは 1 本（系列ごとの本数に増えない）。"""
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    per_minute = 6
    rows = _rows(4, per_minute=per_minute)
    clock = FixedClock(_utc(_T0 + dt.timedelta(minutes=3, seconds=20)))

    # Act
    got = usecases.PublishSeries(
        refs=(_SPREAD_REF, _PLAIN_REF), data_dir=tmp_path, clock=clock
    )(rows)

    # Assert
    assert got.bars == {_SPREAD_REF: 3, _PLAIN_REF: 3}
    assert got.pending_rows == rows[-per_minute:]
