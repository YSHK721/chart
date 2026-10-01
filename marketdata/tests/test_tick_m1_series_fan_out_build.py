"""日別ティック parquet から**複数系列**の M1 を 1 回の走査で作る口（ISSUE-533 段階 3 の前提工事）。

用語（初出定義）:
    組（系列の組）
        ＝ 同じティック木を読む datasetRef の全体。所有者は台帳（``dataset_registry.refs_of_tick_token``）。
    1 回の走査
        ＝ 期間内の日別 parquet を 1 ファイル 1 回だけ読み、1 回だけ畳み（唯一の畳み点は
          ``marketdata.tick_m1._fold_ticks``）、その結果を各系列へ配ること。系列ごとに口を呼ぶと
          同じ parquet を系列の数だけ読んで畳む（出力は正しいままなので状態検証では原理的に
          落ちない・ISSUE-450 と同型）。
    先端（tip）
        ＝ 既存 M1 CSV の最終バーの分。健全な先端が無い系列（ファイル不在・空・末尾 torn）は
          全構築を要る側である（ISSUE-455 の自己修復）。
    発行 / 使用
        発行 ＝ ティック parquet の読込回数、および唯一の畳み点へ渡った回数。
        使用 ＝ その期間に実在する日別 parquet の数（出力に使う素材の数）。
        規約の形は「発行 − 使用 = 0」（絶対命令 2026-08-28）。

本検定が固定するもの:
  B-1 組の**全系列**が書かれ、spread 列を持つのは宣言した側だけである。
  B-2 組で書いた各系列の CSV は、その系列を**単独で**書いた CSV と **byte 一致**する
      （既存系列の出力が 1 バイトも変わらないことの壁。射影で作った側も、単独で畳んだ側と
      同じ値・同じ書式になる）。
  B-3 組への追記で全系列の先端が揃う。
  B-4 健全な先端を持たない系列は**その系列だけ**全構築され、先端を持つ兄弟は**追記**される
      （兄弟の既存 CSV は前の中身を接頭辞に持つ＝全書換していない。R-2/Y-2 の再来を防ぐ壁）。
  B-5 既存の 1 系列の口（``build_m1_from_ticks`` / ``append_m1_from_ticks``）は、1 要素の組を
      通した結果と byte 一致する（薄い包みにしても出力が変わらない）。
  B-6 書込を発行する順は**列の上位集合が先**（2 ファイルを原子的に書く手段は無いので、途中で
      死んだときに遅れるのを「最も見られている系列」にする）。
  CX-1 系列を 1 → 2 にしても parquet の読込と畳みの発行が増えない。発行 − 使用 = 0。
       規模 2 点（日数 1 日と 3 日 × 系列数 1 と 2）。**回数そのものは期待値に焼き込まない**。
  CX-2 追記でも同じ（周期ごとに系列の数だけ読み直さない）。

本検定が固定しないもの（射程の明示）:
  - 常駐（``tools/live_tick_watch.py``）の結線。それは
    ``tools/tests/test_live_tick_watch_series_set.py`` が持つ。
  - 台帳の宣言そのもの。それは ``marketdata/tests/test_dukascopy_spread_series_ledger.py``。

書込はすべて ``tmp_path``（``data/marketdata/**`` は 1 バイトも触らない）。合成系列の組み立ては
``marketdata/tests/spread_series_fixture.py`` が唯一源。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, NamedTuple

import pandas as pd
import pytest

from marketdata import csv_schema, dataset_registry, tick_m1
from marketdata.dataset_registry import REGISTRY
from spread_series_fixture import (
    TICK_TREE,
    cleared_point_cache,  # noqa: F401  （import した先で autouse になる共有 fixture）
    day,
    put_day_of_spread_points,
)

#: Dukascopy 配信のティックであることを示すベンダ欄の値（台帳の語彙）。
_DUKASCOPY = "dukascopy"

#: 合成ティックの分ごとの気配幅（整数 points・分内で変わる）。
_WIDTHS = tuple((63 + m, 60 + m, 66 + m) for m in range(4))


def _series_set() -> "tuple[str, ...]":
    """同じ Dukascopy のティック木を読む系列の組（並びは台帳の宣言順・綴りを書き写さない）。

    組の中身が正しいこと（宣言の在る 1 件と無い 1 件の対）は
    ``marketdata/tests/test_dukascopy_spread_series_ledger.py`` の D-1 が固定する。
    """
    tokens = [d.tick_token for d in REGISTRY.values() if d.tick and d.vendor == _DUKASCOPY]
    return dataset_registry.refs_of_tick_token(tokens[0])


def _spread_ref() -> str:
    """組のうち spread 列を宣言する系列（列の上位集合）。"""
    declared = [
        ref for ref in _series_set()
        if dataset_registry.spread_point_snapshot_of(ref) is not None
    ]
    return declared[0]


def _plain_refs() -> "tuple[str, ...]":
    """組のうち spread 列を持たない系列（最後に書かれる側）。"""
    return tuple(ref for ref in _series_set() if ref != _spread_ref())


def _m1_path(ref: str, data_dir: Path) -> Path:
    return tick_m1.m1_csv_path(ref=ref, data_dir=data_dir)


def _columns(path: Path) -> "list[str]":
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")


def _data_rows(path: Path) -> int:
    return max(len(path.read_text(encoding="utf-8").splitlines()) - 1, 0)


def _put(data_dir: Path, n_days: int) -> "list[pd.Timestamp]":
    """``n_days`` 日ぶんの合成ティックを木へ置き、その日付を返す。"""
    days = [day(k) for k in range(n_days)]
    for when in days:
        put_day_of_spread_points(data_dir, when, _WIDTHS)
    return days


class _RecordingWriter:
    """書き手境界 「`M1Writer`」 の記録つき実装（発行順と発行種別を観測する Test Spy）。"""

    def __init__(self) -> None:
        self.calls: "List[tuple[str, Path]]" = []
        self._real = tick_m1.CsvM1Writer()

    def write_whole(self, m1: pd.DataFrame, path: Any) -> None:
        self.calls.append(("write_whole", Path(path)))
        self._real.write_whole(m1, path)

    def append(self, m1_new: pd.DataFrame, path: Any) -> None:
        self.calls.append(("append", Path(path)))
        self._real.append(m1_new, path)


class _Issued(NamedTuple):
    """1 回の書き手呼出で観測した発行（parquet 読込・畳み）と、出力に使った素材の数。"""

    reads: int
    folds: int
    used: int


def _spy_issues(monkeypatch) -> "tuple[list, list]":
    """parquet の読込と唯一の畳み点を包み、発行ごとに記録する。

    畳みは公開の口の入口ではなく共通前段（``tick_m1._fold_ticks``）で数える。入口で数えると、
    委譲先の内部で 2 回畳む変異が 1 回に見える。
    """
    reads: "list[Any]" = []
    folds: "list[int]" = []
    real_read = pd.read_parquet
    real_fold = tick_m1._fold_ticks

    def read_spy(*args, **kwargs):
        reads.append(args[0] if args else kwargs.get("path"))
        return real_read(*args, **kwargs)

    def fold_spy(ticks, *args, **kwargs):
        folds.append(len(ticks))
        return real_fold(ticks, *args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", read_spy)
    monkeypatch.setattr(tick_m1, "_fold_ticks", fold_spy)
    return reads, folds


# =====================================================================
# B-1. 組の全系列が書かれる
# =====================================================================
def test_the_build_writes_every_series_of_the_set(tmp_path):
    """B-1: 1 回の呼出で組の全系列の M1 が出来、spread 列を持つのは宣言した側だけである。"""
    # Arrange
    refs = _series_set()
    days = _put(tmp_path, 1)

    # Act
    outs = tick_m1.build_m1_from_ticks_for_series(
        days[0], days[-1], refs=refs, symbol=TICK_TREE, data_dir=tmp_path
    )

    # Assert
    assert len(refs) >= 2, f"同じ木を読む系列が {len(refs)} 件（この検定が空振りしている）"
    assert outs == {ref: _m1_path(ref, tmp_path) for ref in refs}
    assert {ref: _data_rows(path) > 0 for ref, path in outs.items()} == {
        ref: True for ref in refs
    }
    assert _columns(outs[_spread_ref()])[-1] == csv_schema.SPREAD_COLUMN
    assert {
        ref: csv_schema.SPREAD_COLUMN in _columns(outs[ref]) for ref in _plain_refs()
    } == {ref: False for ref in _plain_refs()}


# =====================================================================
# B-2. 組で書いた各系列は、単独で書いたものと byte 一致
# =====================================================================
@pytest.mark.parametrize("ref", _series_set())
def test_a_series_written_in_the_set_is_byte_identical_to_one_written_alone(tmp_path, ref):
    """B-2: 組の走査で書いた CSV は、その系列だけを書いた CSV と byte 一致する。

    既存系列（spread を持たない側）については「新しい口を通しても出力が 1 バイトも変わらない」
    ことの壁であり、新系列については「射影で配った値が単独で畳んだ値と同じ」ことの壁である。
    """
    # Arrange
    together_dir = tmp_path / "together"
    alone_dir = tmp_path / "alone"
    days = _put(together_dir, 2)
    _put(alone_dir, 2)

    # Act
    together = tick_m1.build_m1_from_ticks_for_series(
        days[0], days[-1], refs=_series_set(), symbol=TICK_TREE, data_dir=together_dir
    )[ref]
    alone = tick_m1.build_m1_from_ticks(
        days[0], days[-1], ref=ref, symbol=TICK_TREE, data_dir=alone_dir
    )

    # Assert
    assert _data_rows(alone) > 0  # 空振り防止（実際に書いている）
    assert together.read_bytes() == alone.read_bytes()


# =====================================================================
# B-3. 追記で組の先端が揃う
# =====================================================================
def test_appending_to_the_set_leaves_every_series_at_the_same_tip(tmp_path):
    """B-3: 組へ追記すると、全系列の先端が同じ分で揃う。"""
    # Arrange
    refs = _series_set()
    days = _put(tmp_path, 1)
    tick_m1.build_m1_from_ticks_for_series(
        days[0], days[-1], refs=refs, symbol=TICK_TREE, data_dir=tmp_path
    )
    grown = _put(tmp_path, 2)

    # Act
    outs = tick_m1.append_m1_from_ticks_for_series(
        grown[0], grown[-1], refs=refs, symbol=TICK_TREE, data_dir=tmp_path
    )

    # Assert
    tips = {ref: tick_m1.last_m1_date(path) for ref, path in outs.items()}
    assert set(tips) == set(refs)
    assert len(set(tips.values())) == 1, f"追記後も先端が揃っていません: {tips}"


# =====================================================================
# B-4. 先端を持たない系列だけが全構築される（兄弟は追記）
# =====================================================================
def test_a_series_without_a_tip_is_built_whole_while_its_sibling_is_only_appended(tmp_path):
    """B-4: 片方だけ実体が無い組では、その系列だけが全構築され、兄弟は追記される。

    兄弟の既存 CSV が全書換されないことを**接頭辞**で測る（既存系列を書き換える経路は
    ISSUE-511 前提 (a) で実測された R-2/Y-2 そのものである）。
    """
    # Arrange: 兄弟（spread 無し側）だけを 1 日分作る。新系列の置き場は空のまま。
    sibling = _plain_refs()[0]
    newcomer = _spread_ref()
    days = _put(tmp_path, 1)
    tick_m1.build_m1_from_ticks(
        days[0], days[-1], ref=sibling, symbol=TICK_TREE, data_dir=tmp_path
    )
    before = _m1_path(sibling, tmp_path).read_bytes()
    grown = _put(tmp_path, 2)
    writer = _RecordingWriter()

    # Act
    outs = tick_m1.append_m1_from_ticks_for_series(
        grown[0], grown[-1], refs=_series_set(), symbol=TICK_TREE, data_dir=tmp_path,
        writer=writer,
    )

    # Assert
    after = outs[sibling].read_bytes()
    assert after.startswith(before), "兄弟の既存 CSV が全書換されました（追記になっていない）"
    assert len(after) > len(before)  # 空振り防止（実際に伸びた）
    assert ("write_whole", outs[newcomer]) in writer.calls
    assert ("append", outs[sibling]) in writer.calls
    assert ("write_whole", outs[sibling]) not in writer.calls


# =====================================================================
# B-5. 既存の 1 系列の口は薄い包みにしても出力が変わらない
# =====================================================================
@pytest.mark.parametrize("ref", _series_set())
def test_the_single_ref_append_equals_a_one_element_set(tmp_path, ref):
    """B-5: ``append_m1_from_ticks`` の出力は、1 要素の組を通した出力と byte 一致する。"""
    # Arrange
    one_dir = tmp_path / "one"
    set_dir = tmp_path / "set"
    days = _put(one_dir, 1)
    _put(set_dir, 1)
    tick_m1.build_m1_from_ticks(days[0], days[-1], ref=ref, symbol=TICK_TREE, data_dir=one_dir)
    tick_m1.build_m1_from_ticks(days[0], days[-1], ref=ref, symbol=TICK_TREE, data_dir=set_dir)
    grown_one = _put(one_dir, 2)
    _put(set_dir, 2)

    # Act
    single = tick_m1.append_m1_from_ticks(
        grown_one[0], grown_one[-1], ref=ref, symbol=TICK_TREE, data_dir=one_dir
    )
    as_set = tick_m1.append_m1_from_ticks_for_series(
        grown_one[0], grown_one[-1], refs=(ref,), symbol=TICK_TREE, data_dir=set_dir
    )[ref]

    # Assert
    assert _data_rows(single) > 0  # 空振り防止
    assert single.read_bytes() == as_set.read_bytes()


# =====================================================================
# B-6. 書込順は列の上位集合が先
# =====================================================================
def test_the_superset_series_is_written_before_the_most_watched_one(tmp_path):
    """B-6: 発行順は spread 列を持つ側が先である（遅れるのは最も見られている系列）。"""
    # Arrange
    days = _put(tmp_path, 1)
    writer = _RecordingWriter()

    # Act
    outs = tick_m1.build_m1_from_ticks_for_series(
        days[0], days[-1], refs=_series_set(), symbol=TICK_TREE, data_dir=tmp_path,
        writer=writer,
    )

    # Assert
    expected = [
        ("write_whole", outs[ref]) for ref in (_spread_ref(), *_plain_refs())
    ]
    assert writer.calls == expected


# =====================================================================
# CX-1 / CX-2. 系列を増やしても parquet 読込と畳みは増えない
# =====================================================================
def _issued_for_build(monkeypatch, data_dir: Path, refs, n_days: int) -> _Issued:
    """``refs`` を ``n_days`` 日で build したときの発行と、出力に使った素材の数。"""
    days = _put(data_dir, n_days)
    reads, folds = _spy_issues(monkeypatch)
    tick_m1.build_m1_from_ticks_for_series(
        days[0], days[-1], refs=refs, symbol=TICK_TREE, data_dir=data_dir
    )
    used = len(
        tick_m1.day_parquet_files(days[0], days[-1], symbol=TICK_TREE, data_dir=data_dir)
    )
    return _Issued(reads=len(reads), folds=len(folds), used=used)


@pytest.mark.parametrize("n_days", [1, 3])
def test_cx1_the_build_reads_and_folds_each_day_once_for_the_whole_set(
    tmp_path, monkeypatch, n_days
):
    """CX-1: 系列 1 → 2 で parquet 読込・畳みの発行が増えず、発行 − 使用 = 0。

    固定するのは**無駄の不在**であり、回数そのもの（N 回）は期待値に焼き込まない（期待値は
    その期間に実在する日別 parquet の数から導く）。系列ごとに口を呼ぶ形へ戻す変異は、
    系列 1 と 2 の差としてここに現れる。
    """
    # Arrange / Act
    one = _issued_for_build(monkeypatch, tmp_path / f"one{n_days}", (_spread_ref(),), n_days)
    two = _issued_for_build(monkeypatch, tmp_path / f"two{n_days}", _series_set(), n_days)

    # Assert
    assert one.used == n_days  # 空振り防止（素材が実在する）
    assert (one.reads - one.used, one.folds - one.used) == (0, 0)
    assert (two.reads - two.used, two.folds - two.used) == (0, 0), (
        f"系列 2 で 読込 {two.reads} / 畳み {two.folds} − 使用 {two.used} ≠ 0"
    )
    assert (two.reads, two.folds) == (one.reads, one.folds), (
        f"系列を 1 → 2 にしたら 読込 {one.reads} → {two.reads} /"
        f" 畳み {one.folds} → {two.folds} へ増えました（系列ごとに読み直しています）。"
    )


def _issued_for_append(monkeypatch, data_dir: Path, refs, n_days: int) -> _Issued:
    """``refs`` を 1 日分 build 済みにしてから ``n_days`` 日で append したときの発行。"""
    seed = _put(data_dir, 1)
    tick_m1.build_m1_from_ticks_for_series(
        seed[0], seed[-1], refs=refs, symbol=TICK_TREE, data_dir=data_dir
    )
    days = _put(data_dir, n_days + 1)
    reads, folds = _spy_issues(monkeypatch)
    tick_m1.append_m1_from_ticks_for_series(
        days[0], days[-1], refs=refs, symbol=TICK_TREE, data_dir=data_dir
    )
    # 追記の窓は「既存最終バー日から終端まで」＝先端を含む 1 日 ＋ 新しい ``n_days`` 日。
    #   先端の日を読み直すのは無駄ではなく resume 規則そのものである（途中までしか書けていない日を
    #   その周期で埋めるため）。窓の広さは系列数に依らない——それが本検定の主張である。
    return _Issued(reads=len(reads), folds=len(folds), used=n_days + 1)


@pytest.mark.parametrize("n_days", [1, 3])
def test_cx2_the_append_reads_and_folds_the_resume_window_once_for_the_whole_set(
    tmp_path, monkeypatch, n_days
):
    """CX-2: 追記でも系列 1 → 2 で発行が増えず、発行 − 使用 = 0（規模 2 点）。"""
    # Arrange / Act
    one = _issued_for_append(monkeypatch, tmp_path / f"one{n_days}", (_spread_ref(),), n_days)
    two = _issued_for_append(monkeypatch, tmp_path / f"two{n_days}", _series_set(), n_days)

    # Assert
    assert one.reads > 0  # 空振り防止（実際に読んだ）
    assert (one.reads - one.used, one.folds - one.used) == (0, 0)
    assert (two.reads - two.used, two.folds - two.used) == (0, 0), (
        f"系列 2 で 読込 {two.reads} / 畳み {two.folds} − 使用 {two.used} ≠ 0"
    )
    assert (two.reads, two.folds) == (one.reads, one.folds), (
        f"追記で系列を 1 → 2 にしたら 読込 {one.reads} → {two.reads} /"
        f" 畳み {one.folds} → {two.folds} へ増えました。"
    )
